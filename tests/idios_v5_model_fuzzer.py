#!/usr/bin/env python3
"""
Idios contract funds-conservation fuzzer.

A faithful Python model of the Idios v4 contract state machine
(idios_contract.cpp at 37ea6c4), driven by randomized call sequences,
asserting two properties after every successful call and at the end of
every sequence:

  SAFETY  (conservation): for every job, the total amount ever unlocked
          attributable to that job never exceeds the total ever locked
          for it. No path may pay out the same portion twice or pay out
          funds locked for a different job.

  LIVENESS (drainability): after any call sequence, once enough blocks
          pass, greedily applying the recovery methods (refund, claim,
          claim_after_timeout, void_dispute, void claims, sweep) drains
          every job completely: locked == unlocked. No funds are ever
          permanently stuck.

The model mirrors each method's Halt conditions and FundsLock/FundsUnlock
effects exactly. Signatures are assumed satisfied (the fuzzer plays all
parties); conservation must hold regardless of who signs, since the chain
enforces signatures, not amounts.

Run:  python3 idios_conservation_fuzzer.py [num_sequences] [seed]
"""

import random
import sys

# ---------------------------------------------------------------- statuses
OPEN, ACTIVE, AWAITING, DISPUTED, SETTLED, REFUNDED, R_ALICE, R_BOB, CLOSED, VOIDED, CANCELLED = range(11)
STATUS_NAMES = ["Open", "Active", "AwaitingApproval", "Disputed", "Settled",
                "Refunded", "ResolvedToAlice", "ResolvedToBob", "Closed", "Voided",
                "Cancelled"]

MODE_A, MODE_B = "A", "B"


class Halt(Exception):
    """Contract Env::Halt. The call fails, no state change, no funds move."""


class Job:
    __slots__ = ("job_id", "mode", "status", "payment", "collateral",
                 "dispute_fee", "expiry", "review_window", "review_deadline",
                 "dispute_filed", "result_hash", "delivery_hash",
                 "required_collateral", "spec_hash")

    def __init__(self, job_id, mode, payment, dispute_fee, expiry, review_window, result_hash,
                 required_collateral=0, spec_hash=0):
        self.job_id = job_id
        self.mode = mode
        self.status = OPEN
        self.payment = payment
        self.collateral = 0
        self.dispute_fee = dispute_fee     # Mode A: 0
        self.expiry = expiry
        self.review_window = review_window  # Mode A: 0
        self.review_deadline = 0
        self.dispute_filed = 0
        self.result_hash = result_hash      # Mode A only; model as small int
        self.delivery_hash = None
        self.required_collateral = required_collateral  # v5: floor for Commit, 0 = no floor
        self.spec_hash = spec_hash                      # v5: stored only, no funds logic


class Chain:
    """The contract environment: height, jobs, params, and the funds ledger."""

    def __init__(self, arbitrator_timeout, default_review_window):
        self.height = 1000
        self.jobs = {}
        self.arbitrator_timeout = arbitrator_timeout
        self.default_review_window = default_review_window
        # ledger: job_id -> [locked_total, unlocked_total]
        self.ledger = {}

    # ledger helpers -- mirror FundsLock / FundsUnlock, attributed per job
    def lock(self, job_id, amount):
        self.ledger.setdefault(job_id, [0, 0])[0] += amount

    def unlock(self, job_id, amount):
        self.ledger.setdefault(job_id, [0, 0])[1] += amount

    # ------------------------------------------------------------ methods
    # Each mirrors the contract's Halt conditions in order, then effects.

    def create_a(self, job_id, payment, expiry, result_hash,
                 required_collateral=0, spec_hash=0):
        if payment == 0: raise Halt
        if result_hash == 0: raise Halt          # Memis0(result_hash)
        if expiry <= self.height: raise Halt
        if job_id in self.jobs: raise Halt       # JobIdInUse
        job = Job(job_id, MODE_A, payment, 0, expiry, 0, result_hash,
                  required_collateral, spec_hash)
        self.lock(job_id, payment)               # FundsLock(payment)
        self.jobs[job_id] = job

    def create_b(self, job_id, payment, dispute_fee, expiry, review_window,
                 required_collateral=0, spec_hash=0):
        if payment == 0: raise Halt
        if dispute_fee == 0: raise Halt
        # v5: review_window == 0 falls back to the contract default instead of halting
        if review_window == 0:
            review_window = self.default_review_window
        if expiry <= self.height: raise Halt
        if job_id in self.jobs: raise Halt
        job = Job(job_id, MODE_B, payment, dispute_fee, expiry, review_window, 0,
                  required_collateral, spec_hash)
        self.lock(job_id, payment)
        self.jobs[job_id] = job

    def commit(self, job_id, collateral):
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.status != OPEN: raise Halt
        if self.height >= job.expiry: raise Halt
        if collateral == 0: raise Halt
        if collateral < job.required_collateral: raise Halt   # v5: floor
        self.lock(job_id, collateral)            # FundsLock(collateral)
        job.collateral = collateral
        job.status = ACTIVE

    def refund(self, job_id):
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.status not in (OPEN, ACTIVE): raise Halt
        if self.height <= job.expiry: raise Halt
        self.unlock(job_id, job.payment)         # payment only; collateral forfeits
        job.status = REFUNDED

    def submit_delivery(self, job_id, delivery_hash):
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.status != ACTIVE: raise Halt
        if self.height >= job.expiry: raise Halt
        job.delivery_hash = delivery_hash
        if job.mode == MODE_A:
            if delivery_hash != job.result_hash: raise Halt
            self.unlock(job_id, job.payment + job.collateral)
            job.status = CLOSED                  # Mode A auto-settles terminal
        else:
            job.review_deadline = self.height + job.review_window
            job.status = AWAITING

    def approve(self, job_id):
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.mode != MODE_B: raise Halt
        if job.status != AWAITING: raise Halt
        if self.height > job.review_deadline: raise Halt
        job.status = SETTLED

    def dispute(self, job_id):
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.mode != MODE_B: raise Halt
        if job.status != AWAITING: raise Halt
        if self.height > job.review_deadline: raise Halt
        self.lock(job_id, job.dispute_fee)       # FundsLock(dispute_fee)
        job.dispute_filed = self.height
        job.status = DISPUTED

    def resolve_alice(self, job_id):
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.mode != MODE_B: raise Halt
        if job.status != DISPUTED: raise Halt
        job.status = R_ALICE

    def resolve_bob(self, job_id):
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.mode != MODE_B: raise Halt
        if job.status != DISPUTED: raise Halt
        job.status = R_BOB

    def claim_after_timeout(self, job_id):
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.mode != MODE_B: raise Halt
        if job.status != AWAITING: raise Halt
        if self.height <= job.review_deadline: raise Halt
        job.status = SETTLED

    def claim(self, job_id):
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.status == SETTLED:
            self.unlock(job_id, job.payment + job.collateral)
        elif job.status == R_BOB:
            self.unlock(job_id, job.payment + job.collateral + job.dispute_fee)
        elif job.status == R_ALICE:
            self.unlock(job_id, job.payment + job.collateral + job.dispute_fee)
        else:
            raise Halt
        job.status = CLOSED

    def void_dispute(self, job_id):
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.status != DISPUTED: raise Halt
        if self.height <= job.dispute_filed + self.arbitrator_timeout: raise Halt
        job.status = VOIDED

    def void_claim_requester(self, job_id):
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.status != VOIDED: raise Halt
        if job.payment == 0: raise Halt
        self.unlock(job_id, job.payment)
        job.payment = 0

    def void_claim_node(self, job_id):
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.status != VOIDED: raise Halt
        if job.collateral == 0: raise Halt
        self.unlock(job_id, job.collateral)
        job.collateral = 0

    def mutual_cancel(self, job_id):
        # v5: both requester and node sign (modelled as satisfied). Valid from
        # Active or AwaitingApproval only. Never from Open (refund covers it),
        # never from Disputed (the arbitrator path owns it). Pays out in the
        # cancel tx itself: payment to requester, collateral to node.
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.status not in (ACTIVE, AWAITING): raise Halt
        self.unlock(job_id, job.payment + job.collateral)
        job.status = CANCELLED

    def sweep(self, job_id):
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.status == REFUNDED and job.collateral > 0:
            self.unlock(job_id, job.collateral)
            job.collateral = 0
        elif job.status == VOIDED and job.dispute_fee > 0:
            self.unlock(job_id, job.dispute_fee)
            job.dispute_fee = 0
        else:
            raise Halt


# ------------------------------------------------------------------ checks

def check_conservation(chain):
    """SAFETY: per job, unlocked never exceeds locked."""
    for job_id, (locked, unlocked) in chain.ledger.items():
        if unlocked > locked:
            raise AssertionError(
                "CONSERVATION VIOLATED job %s: unlocked %s > locked %s (status %s)"
                % (job_id, unlocked, locked,
                   STATUS_NAMES[chain.jobs[job_id].status] if job_id in chain.jobs else "?"))


def drain_everything(chain):
    """LIVENESS: advance time past every gate, then greedily apply every
    recovery method to fixpoint. Returns list of jobs not fully drained."""
    horizon = max([j.expiry for j in chain.jobs.values()] +
                  [j.review_deadline for j in chain.jobs.values()] +
                  [j.dispute_filed + chain.arbitrator_timeout for j in chain.jobs.values()] +
                  [chain.height])
    chain.height = horizon + 2

    recovery = [chain.refund, chain.claim_after_timeout, chain.claim,
                chain.void_dispute, chain.void_claim_requester,
                chain.void_claim_node, chain.sweep]
    progressed = True
    while progressed:
        progressed = False
        for job_id in list(chain.jobs):
            for method in recovery:
                try:
                    method(job_id)
                    progressed = True
                    check_conservation(chain)
                except Halt:
                    pass
            # AwaitingApproval past deadline became Settled via claim_after_timeout,
            # then claim drains it on the next pass of the loop above.

    stuck = []
    for job_id, (locked, unlocked) in chain.ledger.items():
        if locked != unlocked:
            stuck.append((job_id, locked, unlocked,
                          STATUS_NAMES[chain.jobs[job_id].status]))
    return stuck


# ------------------------------------------------------------------- fuzzer

def fuzz_sequence(seed, n_calls=400, n_jobs=8):
    rng = random.Random(seed)
    chain = Chain(arbitrator_timeout=rng.choice([1, 5, 50]),
                  default_review_window=rng.choice([5, 50]))
    job_ids = list(range(1, n_jobs + 1))
    amounts = [1, 2, 100, 100000]

    def rand_call():
        jid = rng.choice(job_ids)
        op = rng.randrange(15)
        if op == 0:
            chain.create_a(jid, rng.choice(amounts),
                           chain.height + rng.choice([1, 2, 5, 30]), result_hash=7,
                           required_collateral=rng.choice([0, 0, 2, 100]),
                           spec_hash=rng.randrange(1000))
        elif op == 1:
            chain.create_b(jid, rng.choice(amounts), rng.choice(amounts),
                           chain.height + rng.choice([1, 2, 5, 30]),
                           rng.choice([0, 1, 3, 10]),
                           required_collateral=rng.choice([0, 0, 2, 100]),
                           spec_hash=rng.randrange(1000))
        elif op == 2:
            chain.commit(jid, rng.choice(amounts))
        elif op == 3:
            chain.refund(jid)
        elif op == 4:
            # right hash half the time, wrong hash half the time
            chain.submit_delivery(jid, rng.choice([7, 9]))
        elif op == 5:
            chain.approve(jid)
        elif op == 6:
            chain.dispute(jid)
        elif op == 7:
            chain.resolve_alice(jid)
        elif op == 8:
            chain.resolve_bob(jid)
        elif op == 9:
            chain.claim_after_timeout(jid)
        elif op == 10:
            chain.claim(jid)
        elif op == 11:
            chain.void_dispute(jid)
        elif op == 12:
            rng.choice([chain.void_claim_requester, chain.void_claim_node])(jid)
        elif op == 13:
            chain.sweep(jid)
        elif op == 14:
            chain.mutual_cancel(jid)

    successes = 0
    status_seen = set()
    for _ in range(n_calls):
        # advance time irregularly so every height gate gets exercised
        if rng.random() < 0.35:
            chain.height += rng.choice([1, 1, 2, 7])
        try:
            rand_call()
            successes += 1
            check_conservation(chain)
        except Halt:
            pass
        for j in chain.jobs.values():
            status_seen.add(j.status)

    stuck = drain_everything(chain)
    if stuck:
        raise AssertionError("LIVENESS VIOLATED seed %s, stuck jobs: %s" % (seed, stuck))
    return successes, status_seen


def main():
    n_seq = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    base = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    total_success = 0
    all_statuses = set()
    for i in range(n_seq):
        s, seen = fuzz_sequence(base + i)
        total_success += s
        all_statuses |= seen
    print("OK: %d sequences, %d successful calls, conservation and liveness held."
          % (n_seq, total_success))
    missing = set(range(11)) - all_statuses
    print("Statuses reached: %s" % sorted(STATUS_NAMES[s] for s in all_statuses))
    if missing:
        print("WARNING statuses never reached: %s (weak coverage)"
              % sorted(STATUS_NAMES[s] for s in missing))


if __name__ == "__main__":
    main()

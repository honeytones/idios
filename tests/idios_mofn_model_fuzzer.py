#!/usr/bin/env python3
"""
Idios contract funds-conservation fuzzer, M of N arbitration model.

Extends the v5 model (idios_v5_model_fuzzer.py) with decentralized
arbitration. The escrow paths are unchanged. New machinery:

  ARBITRATOR REGISTRY. An arbitrator registers and locks a standing stake.
  That stake is a second locked pool, accounted exactly like a job's funds
  (per unit, unlocked never exceeds locked). The arbitrator deregisters and,
  after a cooldown with no bonded open disputes, reclaims the remainder.

  QUORUM RESOLVE. On dispute the eligible arbitrator set is snapshotted and
  the threshold M is frozen (simple majority of the snapshot). Each snapshot
  member with positive stake casts one immutable vote. The Mth matching vote
  resolves the job to Alice or Bob.

  SLASH. At resolution every arbitrator who voted the losing side is slashed
  (full stake, v1) to the treasury, off its own stake unit. Winning-side
  voters and non voters are not slashed.

  REWARD. The dispute_fee no longer goes to the winner. The winner takes
  payment + collateral; the dispute_fee is the arbitration reward, paid to
  the M consensus voters. The job's total payout is unchanged (P + C + F),
  so the per-job ledger is identical to v5; only the recipients move.

  NO QUORUM. Split votes or too few voters never resolve. The existing
  arbitrator_timeout void path handles it unchanged, no slash, fee to
  treasury. Every dispute still terminates.

Two properties asserted after every successful call and at end of sequence:

  SAFETY  (conservation): for every unit (job OR arbitrator stake), total
          unlocked never exceeds total locked.
  LIVENESS (drainability): after any sequence, once enough blocks pass,
          greedily applying recovery (refund, claim, winner_claim,
          arb_reward_claim, claim_after_timeout, void_dispute, void claims,
          sweep) plus deregister/reclaim drains every job AND every stake
          completely: locked == unlocked. Nothing is permanently stuck.

Run:  python3 idios_mofn_model_fuzzer.py [num_sequences] [seed]
"""

import random
import sys

# ---------------------------------------------------------------- statuses
OPEN, ACTIVE, AWAITING, DISPUTED, SETTLED, REFUNDED, R_ALICE, R_BOB, CLOSED, VOIDED, CANCELLED = range(11)
STATUS_NAMES = ["Open", "Active", "AwaitingApproval", "Disputed", "Settled",
                "Refunded", "ResolvedToAlice", "ResolvedToBob", "Closed", "Voided",
                "Cancelled"]

MODE_A, MODE_B = "A", "B"

# arbitrator registry states
A_REG, A_DEREG, A_GONE = "reg", "dereg", "gone"
ALICE, BOB = "alice", "bob"


class Halt(Exception):
    """Contract Env::Halt. The call fails, no state change, no funds move."""


class Job:
    __slots__ = ("job_id", "mode", "status", "payment", "collateral",
                 "dispute_fee", "expiry", "review_window", "review_deadline",
                 "dispute_filed", "result_hash", "delivery_hash",
                 "required_collateral", "spec_hash",
                 # M of N additions:
                 "snapshot", "threshold", "votes", "winner_paid", "fee_paid")

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
        self.required_collateral = required_collateral
        self.spec_hash = spec_hash
        # M of N
        self.snapshot = set()      # arbitrator ids eligible at dispute time
        self.threshold = 0         # M frozen at dispute time
        self.votes = {}            # arb_id -> ALICE / BOB
        self.winner_paid = False   # winner claimed P + C
        self.fee_paid = False      # consensus voters claimed F


class Arb:
    __slots__ = ("arb_id", "stake", "state", "dereg_block")

    def __init__(self, arb_id, stake):
        self.arb_id = arb_id
        self.stake = stake          # remaining stake (reduced by slashes)
        self.state = A_REG
        self.dereg_block = 0


class Chain:
    """The contract environment: height, jobs, arbitrators, params, ledger."""

    def __init__(self, arbitrator_timeout, default_review_window,
                 stake_cooldown, slash_num=1, slash_den=1):
        self.height = 1000
        self.jobs = {}
        self.arbs = {}
        self.arbitrator_timeout = arbitrator_timeout
        self.default_review_window = default_review_window
        self.stake_cooldown = stake_cooldown
        # full slash by default (1/1); fraction is integer-clean
        self.slash_num = slash_num
        self.slash_den = slash_den
        # ledger: unit_key -> [locked_total, unlocked_total]
        # unit_key is a job_id (int) or ("arb", arb_id)
        self.ledger = {}

    # ledger helpers -- mirror FundsLock / FundsUnlock, attributed per unit
    def lock(self, unit, amount):
        self.ledger.setdefault(unit, [0, 0])[0] += amount

    def unlock(self, unit, amount):
        self.ledger.setdefault(unit, [0, 0])[1] += amount

    # ------------------------------------------------- escrow (v5, unchanged)

    def create_a(self, job_id, payment, expiry, result_hash,
                 required_collateral=0, spec_hash=0):
        if payment == 0: raise Halt
        if result_hash == 0: raise Halt
        if expiry <= self.height: raise Halt
        if job_id in self.jobs: raise Halt
        job = Job(job_id, MODE_A, payment, 0, expiry, 0, result_hash,
                  required_collateral, spec_hash)
        self.lock(job_id, payment)
        self.jobs[job_id] = job

    def create_b(self, job_id, payment, dispute_fee, expiry, review_window,
                 required_collateral=0, spec_hash=0):
        if payment == 0: raise Halt
        if dispute_fee == 0: raise Halt
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
        if collateral < job.required_collateral: raise Halt
        self.lock(job_id, collateral)
        job.collateral = collateral
        job.status = ACTIVE

    def refund(self, job_id):
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.status not in (OPEN, ACTIVE): raise Halt
        if self.height <= job.expiry: raise Halt
        self.unlock(job_id, job.payment)
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
            job.status = CLOSED
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

    def claim_after_timeout(self, job_id):
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.mode != MODE_B: raise Halt
        if job.status != AWAITING: raise Halt
        if self.height <= job.review_deadline: raise Halt
        job.status = SETTLED

    def claim(self, job_id):
        # v5 claim now only drains SETTLED (approved or review timeout).
        # Resolved disputes drain via winner_claim + arb_reward_claim.
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.status != SETTLED: raise Halt
        self.unlock(job_id, job.payment + job.collateral)
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

    # ------------------------------------------------- arbitrator registry

    def register(self, arb_id, stake):
        if stake == 0: raise Halt
        a = self.arbs.get(arb_id)
        if a is not None and a.state != A_GONE: raise Halt  # already active
        if a is not None and a.state == A_GONE: raise Halt  # ids not reused
        self.lock(("arb", arb_id), stake)
        self.arbs[arb_id] = Arb(arb_id, stake)

    def deregister(self, arb_id):
        a = self.arbs.get(arb_id)
        if a is None: raise Halt
        if a.state != A_REG: raise Halt
        a.state = A_DEREG
        a.dereg_block = self.height

    def _bonded(self, arb_id):
        # bonded while a snapshot it belongs to is still an open dispute
        for job in self.jobs.values():
            if job.status == DISPUTED and arb_id in job.snapshot:
                return True
        return False

    def reclaim_stake(self, arb_id):
        a = self.arbs.get(arb_id)
        if a is None: raise Halt
        if a.state != A_DEREG: raise Halt
        if self.height <= a.dereg_block + self.stake_cooldown: raise Halt
        if self._bonded(arb_id): raise Halt
        if a.stake > 0:
            self.unlock(("arb", arb_id), a.stake)   # reclaim remainder
            a.stake = 0
        a.state = A_GONE

    # ------------------------------------------------- quorum dispute path

    def dispute(self, job_id):
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.mode != MODE_B: raise Halt
        if job.status != AWAITING: raise Halt
        if self.height > job.review_deadline: raise Halt
        self.lock(job_id, job.dispute_fee)
        job.dispute_filed = self.height
        # snapshot eligible arbitrators and freeze M = simple majority.
        # (C++ also excludes the job's own requester/node; abstract arbs
        #  here are separate entities, so there is nothing to exclude.)
        eligible = {aid for aid, a in self.arbs.items()
                    if a.state in (A_REG, A_DEREG) and a.stake > 0}
        job.snapshot = eligible
        job.threshold = (len(eligible) // 2) + 1 if eligible else 1
        job.votes = {}
        job.status = DISPUTED

    def vote(self, arb_id, job_id, side):
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.mode != MODE_B: raise Halt
        if job.status != DISPUTED: raise Halt
        if arb_id not in job.snapshot: raise Halt
        a = self.arbs.get(arb_id)
        if a is None or a.stake == 0: raise Halt
        if arb_id in job.votes: raise Halt          # one immutable vote
        if side not in (ALICE, BOB): raise Halt
        job.votes[arb_id] = side
        tally = sum(1 for v in job.votes.values() if v == side)
        if tally >= job.threshold:
            self._resolve(job, side)

    def _resolve(self, job, winning_side):
        losing = ALICE if winning_side == BOB else BOB
        # slash every voter who backed the losing side (full stake, v1)
        for aid, v in job.votes.items():
            if v == losing:
                a = self.arbs.get(aid)
                if a and a.stake > 0:
                    amt = (a.stake * self.slash_num) // self.slash_den
                    if amt > a.stake:
                        amt = a.stake
                    if amt > 0:
                        self.unlock(("arb", aid), amt)  # forfeit to treasury
                        a.stake -= amt
        job.status = R_ALICE if winning_side == ALICE else R_BOB

    def winner_claim(self, job_id):
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.status not in (R_ALICE, R_BOB): raise Halt
        if job.winner_paid: raise Halt
        self.unlock(job_id, job.payment + job.collateral)
        job.winner_paid = True
        if job.fee_paid:
            job.status = CLOSED

    def arb_reward_claim(self, job_id):
        # the dispute_fee, split among the M consensus voters. Model tracks
        # the total only; resolution guarantees at least one consensus voter.
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.status not in (R_ALICE, R_BOB): raise Halt
        if job.fee_paid: raise Halt
        self.unlock(job_id, job.dispute_fee)
        job.fee_paid = True
        if job.winner_paid:
            job.status = CLOSED


# ------------------------------------------------------------------ checks

def check_conservation(chain):
    """SAFETY: per unit (job or stake), unlocked never exceeds locked."""
    for unit, (locked, unlocked) in chain.ledger.items():
        if unlocked > locked:
            label = "?"
            if isinstance(unit, int) and unit in chain.jobs:
                label = STATUS_NAMES[chain.jobs[unit].status]
            elif isinstance(unit, tuple):
                label = "stake"
            raise AssertionError(
                "CONSERVATION VIOLATED unit %s: unlocked %s > locked %s (%s)"
                % (unit, unlocked, locked, label))


def drain_everything(chain):
    """LIVENESS: advance past every gate, then greedily apply every recovery
    method plus deregister/reclaim to fixpoint. Returns units not fully
    drained."""
    horizon = max([j.expiry for j in chain.jobs.values()] +
                  [j.review_deadline for j in chain.jobs.values()] +
                  [j.dispute_filed + chain.arbitrator_timeout for j in chain.jobs.values()] +
                  [a.dereg_block + chain.stake_cooldown for a in chain.arbs.values()] +
                  [chain.height])
    chain.height = horizon + 2

    job_recovery = [chain.refund, chain.claim_after_timeout, chain.claim,
                    chain.winner_claim, chain.arb_reward_claim,
                    chain.void_dispute, chain.void_claim_requester,
                    chain.void_claim_node, chain.sweep]

    def run_jobs():
        progressed = True
        while progressed:
            progressed = False
            for job_id in list(chain.jobs):
                for method in job_recovery:
                    try:
                        method(job_id)
                        progressed = True
                        check_conservation(chain)
                    except Halt:
                        pass

    # 1) drive all jobs to terminal (disputes that never reached quorum void
    #    here; resolved disputes get winner + fee claimed)
    run_jobs()
    # 2) deregister every still-active arbitrator
    for aid in list(chain.arbs):
        try:
            chain.deregister(aid)
        except Halt:
            pass
    # 3) jobs are terminal now, so nobody is bonded; pass cooldown
    chain.height += chain.stake_cooldown + 2
    # 4) reclaim every stake remainder (fully slashed arbs reclaim 0)
    for aid in list(chain.arbs):
        try:
            chain.reclaim_stake(aid)
            check_conservation(chain)
        except Halt:
            pass

    stuck = []
    for unit, (locked, unlocked) in chain.ledger.items():
        if locked != unlocked:
            if isinstance(unit, int):
                label = STATUS_NAMES[chain.jobs[unit].status]
            else:
                label = "stake:%s" % (chain.arbs[unit[1]].state,)
            stuck.append((unit, locked, unlocked, label))
    return stuck


# ------------------------------------------------------------------- fuzzer

def fuzz_sequence(seed, n_calls=600, n_jobs=8, n_arbs=8):
    rng = random.Random(seed)
    chain = Chain(arbitrator_timeout=rng.choice([1, 5, 50]),
                  default_review_window=rng.choice([5, 50]),
                  stake_cooldown=rng.choice([1, 5, 20]),
                  slash_num=1, slash_den=1)   # v1: full slash
    job_ids = list(range(1, n_jobs + 1))
    arb_ids = list(range(101, 101 + n_arbs))
    amounts = [1, 2, 100, 100000]

    def rand_call():
        op = rng.randrange(18)
        if op == 0:
            chain.create_a(rng.choice(job_ids), rng.choice(amounts),
                           chain.height + rng.choice([1, 2, 5, 30]), result_hash=7,
                           required_collateral=rng.choice([0, 0, 2, 100]),
                           spec_hash=rng.randrange(1000))
        elif op == 1:
            chain.create_b(rng.choice(job_ids), rng.choice(amounts), rng.choice(amounts),
                           chain.height + rng.choice([1, 2, 5, 30]),
                           rng.choice([0, 1, 3, 10]),
                           required_collateral=rng.choice([0, 0, 2, 100]),
                           spec_hash=rng.randrange(1000))
        elif op == 2:
            chain.commit(rng.choice(job_ids), rng.choice(amounts))
        elif op == 3:
            chain.refund(rng.choice(job_ids))
        elif op == 4:
            chain.submit_delivery(rng.choice(job_ids), rng.choice([7, 9]))
        elif op == 5:
            chain.approve(rng.choice(job_ids))
        elif op == 6:
            chain.dispute(rng.choice(job_ids))
        elif op == 7:
            chain.claim_after_timeout(rng.choice(job_ids))
        elif op == 8:
            chain.claim(rng.choice(job_ids))
        elif op == 9:
            chain.void_dispute(rng.choice(job_ids))
        elif op == 10:
            rng.choice([chain.void_claim_requester, chain.void_claim_node])(rng.choice(job_ids))
        elif op == 11:
            chain.sweep(rng.choice(job_ids))
        elif op == 12:
            chain.mutual_cancel(rng.choice(job_ids))
        elif op == 13:
            chain.winner_claim(rng.choice(job_ids))
        elif op == 14:
            chain.arb_reward_claim(rng.choice(job_ids))
        elif op == 15:
            chain.register(rng.choice(arb_ids), rng.choice(amounts))
        elif op == 16:
            rng.choice([chain.deregister, chain.reclaim_stake])(rng.choice(arb_ids))
        elif op == 17:
            # bias votes onto a real disputed job by a snapshot member, so
            # quorum actually gets reached and the resolve/slash paths run
            disputed = [j for j in chain.jobs.values() if j.status == DISPUTED and j.snapshot]
            if disputed:
                job = rng.choice(disputed)
                aid = rng.choice(list(job.snapshot))
                chain.vote(aid, job.job_id, rng.choice([ALICE, BOB]))
            else:
                chain.vote(rng.choice(arb_ids), rng.choice(job_ids), rng.choice([ALICE, BOB]))

    successes = 0
    status_seen = set()
    for _ in range(n_calls):
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
        raise AssertionError("LIVENESS VIOLATED seed %s, stuck units: %s" % (seed, stuck))
    return successes, status_seen


def fuzz_mofn_scenario(seed, counters):
    """Force the new path: register arbs, drive a Mode B job to a filed
    dispute, then deterministically reach a quorum (with losing voters to
    slash) or a deliberate split (to void). Assert the expected terminal,
    the slash effect, and conservation/liveness."""
    rng = random.Random(seed)
    chain = Chain(arbitrator_timeout=rng.choice([20, 50, 100]),  # room to vote
                  default_review_window=rng.choice([5, 50]),
                  stake_cooldown=rng.choice([1, 5, 20]),
                  slash_num=1, slash_den=1)
    amounts = [1, 2, 100, 100000]

    n = rng.randint(1, 6)
    arb_ids = list(range(101, 101 + n))
    stakes = {}
    for aid in arb_ids:
        s = rng.choice(amounts)
        chain.register(aid, s)
        stakes[aid] = s

    jid = 1
    payment = rng.choice(amounts)
    fee = rng.choice(amounts)
    chain.create_b(jid, payment, fee, chain.height + 200, rng.choice([0, 5, 10]))
    chain.commit(jid, rng.choice(amounts))
    chain.submit_delivery(jid, rng.choice([7, 9]))   # Mode B ignores the hash
    chain.dispute(jid)
    job = chain.jobs[jid]
    M = job.threshold
    snap = sorted(job.snapshot)
    assert len(snap) == n and M == (n // 2) + 1

    outcome = rng.choice(["alice", "bob", "split"])

    if outcome in ("alice", "bob"):
        win = ALICE if outcome == "alice" else BOB
        lose = BOB if win == ALICE else ALICE
        rng.shuffle(snap)
        winners = snap[:M]                     # exactly M consensus voters
        pre_losers = snap[M:][:max(M - 1, 0)]  # vote losing, but below quorum
        # cast losing votes first (recorded, will be slashed), none reaches M
        for a in pre_losers:
            chain.vote(a, jid, lose)
        assert job.status == DISPUTED, (seed, "premature resolve")
        # now cast the M winning votes; the Mth resolves
        for a in winners:
            chain.vote(a, jid, win)
        assert job.status == (R_ALICE if win == ALICE else R_BOB), \
            (seed, "expected resolve", job.status, M, n)
        # every pre-loser must be fully slashed: its stake unit shows the
        # whole stake unlocked (forfeit to treasury) right now
        for a in pre_losers:
            locked, unlocked = chain.ledger[("arb", a)]
            assert unlocked == stakes[a] and chain.arbs[a].stake == 0, \
                (seed, "slash missing", a, locked, unlocked, stakes[a])
        counters["resolved_" + win] += 1
        counters["slashed"] += len(pre_losers)
    else:
        # deliberate split: at most M-1 on each side, never a quorum
        rng.shuffle(snap)
        half = M - 1
        for a in snap[:half]:
            chain.vote(a, jid, ALICE)
        for a in snap[half:half + half]:
            chain.vote(a, jid, BOB)
        assert job.status == DISPUTED, (seed, "split unexpectedly resolved")
        counters["split"] += 1

    # drive everything to terminal and assert full drain
    stuck = drain_everything(chain)
    if stuck:
        raise AssertionError("SCENARIO LIVENESS VIOLATED seed %s: %s" % (seed, stuck))

    # post-drain shape checks
    if outcome in ("alice", "bob"):
        assert job.status == CLOSED and job.winner_paid and job.fee_paid, \
            (seed, "resolved job not fully claimed", STATUS_NAMES[job.status])
    else:
        assert job.status == VOIDED, (seed, "split job not voided", STATUS_NAMES[job.status])
    # every stake unit fully drained (slash + reclaim == stake)
    for aid in arb_ids:
        locked, unlocked = chain.ledger[("arb", aid)]
        assert locked == unlocked == stakes[aid], (seed, "stake not drained", aid)


def main():
    n_seq = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    base = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    total_success = 0
    all_statuses = set()
    for i in range(n_seq):
        s, seen = fuzz_sequence(base + i)
        total_success += s
        all_statuses |= seen
    print("OK: %d sequences, %d successful calls, conservation and liveness held."
          % (n_seq, total_success))
    print("Statuses reached: %s" % sorted(STATUS_NAMES[s] for s in all_statuses))
    missing = set(range(11)) - all_statuses
    if missing:
        print("WARNING statuses never reached: %s (weak coverage)"
              % sorted(STATUS_NAMES[s] for s in missing))

    # structured M of N coverage: forced quorum, slash, and split-to-void
    counters = {"resolved_alice": 0, "resolved_bob": 0, "slashed": 0, "split": 0}
    for i in range(n_seq):
        fuzz_mofn_scenario(base + i, counters)
    print("M of N scenarios: %d, resolved Alice %d, resolved Bob %d, "
          "losing voters slashed %d, split-to-void %d. conservation and liveness held."
          % (n_seq, counters["resolved_alice"], counters["resolved_bob"],
             counters["slashed"], counters["split"]))


if __name__ == "__main__":
    main()

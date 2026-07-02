#!/usr/bin/env python3
"""
Idios contract funds-conservation fuzzer, v2 model:
M of N arbitration (v1, unchanged) + arbitrator registry hardening + the
worker reputation bond with slash.

Extends the M of N model (idios_mofn_model_fuzzer.py). The escrow and quorum
paths are carried over unchanged. This stays the on-chain-faithful shape: a
BVM method cannot iterate storage, so every per-key effect is its own keyed
call, exactly as the contract will do it.

New in v2:

  REGISTRY HARDENING (arbitrator side). Register now requires: the bond
  asset is BEAM (asset 0), the bond meets a minimum floor, and the kernel
  carries the admin signature alongside the arbitrator's (curation until an
  arbitrator slash exists). A fully exited identity (state gone) MAY
  re-register, fixing the v1 burned-identity rule; its registered_at is
  fresh, so a re-registration can never regain eligibility on a dispute
  filed during a previous life.

  WORKER BOND (reputation side). A worker (the job's node key) may lock a
  standing bond to be scoreable. BEAM only, no floor (the off chain score
  reader shows the amount, dust is self defeating). Lifecycle mirrors the
  arbitrator registry: register, deregister, reclaim after the same
  cooldown. The bond is slashable, which is the whole point:

    ENCUMBRANCE. Filing a dispute on a Mode B job whose worker holds a live
    bond (registered or deregistering, stake > 0, not already slashed)
    increments that bond's encumbrance counter and marks the dispute
    bond-encumbered. Reclaim halts while encumbrances > 0, closing the
    escape where a worker deregisters mid dispute and pulls the bond before
    the ruling. A bond registered AFTER the dispute was filed is not
    encumbered by it and is not at risk from it, by design: the bond at
    stake is the one that existed when the dispute was filed.

    SLASH. The quorum resolving a bond-encumbered dispute to Alice (the
    worker lost an arbitrated dispute) puts the whole bond into a slashed
    state: reclaim halts forever, the stake sits locked awaiting the
    treasury. Resolution to Bob, or the dispute voiding on arbitrator
    timeout, releases the encumbrance and leaves the bond untouched. A bond
    is slashed at most once; further encumbered disputes resolving to Alice
    only release their encumbrance.

    SLASH SWEEP. A new treasury method pulls a slashed bond's stake out of
    the contract, after which the identity is gone and the worker may bond
    again from scratch (the slash stays visible in job history for the
    score reader). The sweep must wait for the bond's encumbrance counter
    to reach zero, i.e. for every other dispute that encumbered this bond
    to terminate. Fuzzer-found rule: an early sweep frees the identity to
    re-bond while an old dispute still holds an encumbrance, and that
    dispute's termination would then hit the fresh innocent bond.

Three properties asserted after every successful call and at end of sequence:

  SAFETY  (conservation): for every unit (job, arbitrator bond, or worker
          bond), total unlocked never exceeds total locked.
  LIVENESS (drainability): after any sequence, once enough blocks pass,
          greedily applying recovery (refund, claim, winner_claim, per voter
          arb_reward_claim, sweeps, void paths, deregister/reclaim on both
          registries, slash_sweep) drains every job AND every bond to
          locked == unlocked. Nothing is permanently stuck.
  ENCUMBRANCE INTEGRITY: every worker bond's encumbrance counter equals the
          number of live bond-encumbered disputes naming that worker, at all
          times. A wrong counter either bricks a reclaim (liveness) or opens
          the mid-dispute escape (safety of the slash), so it is checked as
          its own invariant.

Note on enumeration: the CONTRACT may not iterate storage, so each reward
claim, each sweep, each reclaim is a separate keyed method. This fuzzer (the
off-chain model) is allowed to loop, the loop stands in for many independent
transactions.

Run:  python3 idios_v2_model_fuzzer.py [num_sequences] [seed]
"""

import random
import sys

# ---------------------------------------------------------------- statuses
OPEN, ACTIVE, AWAITING, DISPUTED, SETTLED, REFUNDED, R_ALICE, R_BOB, CLOSED, VOIDED, CANCELLED = range(11)
STATUS_NAMES = ["Open", "Active", "AwaitingApproval", "Disputed", "Settled",
                "Refunded", "ResolvedToAlice", "ResolvedToBob", "Closed", "Voided",
                "Cancelled"]

MODE_A, MODE_B = "A", "B"

A_REG, A_DEREG, A_GONE = "reg", "dereg", "gone"
W_REG, W_DEREG, W_SLASHED, W_GONE = "reg", "dereg", "slashed", "gone"
ALICE, BOB = "alice", "bob"

BEAM = 0                 # asset id 0
MIN_ARB_BOND = 100       # model units; the C++ hardcodes the real floor in groth


class Halt(Exception):
    """Contract Env::Halt. The call fails, no state change, no funds move."""


class Job:
    __slots__ = ("job_id", "mode", "status", "payment", "collateral",
                 "dispute_fee", "expiry", "review_window", "review_deadline",
                 "dispute_filed", "result_hash", "delivery_hash",
                 "required_collateral", "spec_hash",
                 # M of N (v1), frozen/recorded on the job itself:
                 "frozen_n", "threshold", "votes", "vc_alice", "vc_bob",
                 "resolution", "fee_share", "fee_remainder",
                 "winner_paid", "fee_claimed", "remainder_swept",
                 # v2 worker bond:
                 "worker", "bond_encumbered")

    def __init__(self, job_id, mode, payment, dispute_fee, expiry, review_window, result_hash,
                 required_collateral=0, spec_hash=0, worker=None):
        self.job_id = job_id
        self.mode = mode
        self.status = OPEN
        self.payment = payment
        self.collateral = 0
        self.dispute_fee = dispute_fee
        self.expiry = expiry
        self.review_window = review_window
        self.review_deadline = 0
        self.dispute_filed = 0
        self.result_hash = result_hash
        self.delivery_hash = None
        self.required_collateral = required_collateral
        self.spec_hash = spec_hash
        # M of N
        self.frozen_n = 0
        self.threshold = 0
        self.votes = {}            # arb_id -> ALICE / BOB (per (job,arb) record)
        self.vc_alice = 0
        self.vc_bob = 0
        self.resolution = None     # None / ALICE / BOB, set once at resolve
        self.fee_share = 0         # F // M
        self.fee_remainder = 0     # F %  M
        self.winner_paid = False
        self.fee_claimed = set()
        self.remainder_swept = False
        # v2
        self.worker = worker       # node identity; None = fresh key, no bond
        self.bond_encumbered = False


class Arb:
    __slots__ = ("arb_id", "stake", "state", "registered_at", "dereg_block")

    def __init__(self, arb_id, stake, registered_at):
        self.arb_id = arb_id
        self.stake = stake
        self.state = A_REG
        self.registered_at = registered_at
        self.dereg_block = 0


class WorkerBond:
    __slots__ = ("worker_id", "stake", "state", "bonded_at", "dereg_block",
                 "encumbrances")

    def __init__(self, worker_id, stake, bonded_at):
        self.worker_id = worker_id
        self.stake = stake
        self.state = W_REG
        self.bonded_at = bonded_at
        self.dereg_block = 0
        self.encumbrances = 0      # live bond-encumbered disputes naming me


class Chain:
    def __init__(self, arbitrator_timeout, default_review_window, stake_cooldown):
        self.height = 1000
        self.jobs = {}
        self.arbs = {}
        self.worker_bonds = {}
        self.n_registered = 0      # live arbitrator bonds (REG or DEREG); the N for quorum
        self.arbitrator_timeout = arbitrator_timeout
        self.default_review_window = default_review_window
        self.stake_cooldown = stake_cooldown
        # unit -> [locked, unlocked]; unit = job_id | ("arb", id) | ("wbond", id)
        self.ledger = {}

    def lock(self, unit, amount):
        self.ledger.setdefault(unit, [0, 0])[0] += amount

    def unlock(self, unit, amount):
        self.ledger.setdefault(unit, [0, 0])[1] += amount

    # ------------------------------------------------- escrow (v5, unchanged)

    def create_a(self, job_id, payment, expiry, result_hash,
                 required_collateral=0, spec_hash=0, worker=None):
        if payment == 0: raise Halt
        if result_hash == 0: raise Halt
        if expiry <= self.height: raise Halt
        if job_id in self.jobs: raise Halt
        job = Job(job_id, MODE_A, payment, 0, expiry, 0, result_hash,
                  required_collateral, spec_hash, worker)
        self.lock(job_id, payment)
        self.jobs[job_id] = job

    def create_b(self, job_id, payment, dispute_fee, expiry, review_window,
                 required_collateral=0, spec_hash=0, worker=None):
        if payment == 0: raise Halt
        if dispute_fee == 0: raise Halt
        if review_window == 0:
            review_window = self.default_review_window
        if expiry <= self.height: raise Halt
        if job_id in self.jobs: raise Halt
        job = Job(job_id, MODE_B, payment, dispute_fee, expiry, review_window, 0,
                  required_collateral, spec_hash, worker)
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
        # Settled (approved or review timeout) only: winner takes P + C.
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
        # v2: a voided dispute releases its encumbrance, the bond is untouched
        if job.bond_encumbered:
            wb = self.worker_bonds[job.worker]
            wb.encumbrances -= 1
            job.bond_encumbered = False

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
        # treasury collects: refunded collateral, voided fee, and the
        # resolved-dispute reward remainder F % M.
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.status == REFUNDED and job.collateral > 0:
            self.unlock(job_id, job.collateral)
            job.collateral = 0
        elif job.status == VOIDED and job.dispute_fee > 0:
            self.unlock(job_id, job.dispute_fee)
            job.dispute_fee = 0
        elif job.resolution is not None and job.fee_remainder > 0 and not job.remainder_swept:
            self.unlock(job_id, job.fee_remainder)
            job.remainder_swept = True
        else:
            raise Halt

    # -------------------------------- arbitrator registry (v2 hardened)

    def register(self, arb_id, stake, asset=BEAM, admin=True):
        # v2 gates: admin co-sign (curation until arbitrator slash exists),
        # BEAM only, minimum floor. A gone identity may re-register; its
        # registered_at is fresh, so no eligibility carries over.
        if not admin: raise Halt
        if asset != BEAM: raise Halt
        if stake < MIN_ARB_BOND: raise Halt         # covers stake == 0
        a = self.arbs.get(arb_id)
        if a is not None and a.state != A_GONE: raise Halt
        self.lock(("arb", arb_id), stake)
        self.arbs[arb_id] = Arb(arb_id, stake, self.height)
        self.n_registered += 1

    def deregister(self, arb_id):
        a = self.arbs.get(arb_id)
        if a is None: raise Halt
        if a.state != A_REG: raise Halt
        a.state = A_DEREG
        a.dereg_block = self.height
        # still counts toward N until fully exited (can still vote on disputes
        # it was eligible for), so n_registered is unchanged here.

    def reclaim_stake(self, arb_id):
        a = self.arbs.get(arb_id)
        if a is None: raise Halt
        if a.state != A_DEREG: raise Halt
        if self.height <= a.dereg_block + self.stake_cooldown: raise Halt
        # No "still bonded to an open dispute" gate. The contract cannot scan
        # jobs, and with no arbitrator slash the bond is never at risk mid
        # dispute; reclaiming early only removes a voter, which is safe.
        self.unlock(("arb", arb_id), a.stake)        # full bond back, never slashed
        a.stake = 0
        a.state = A_GONE
        self.n_registered -= 1

    # -------------------------------- worker bond (v2, reputation)

    def worker_register(self, worker_id, stake, asset=BEAM):
        if stake == 0: raise Halt
        if asset != BEAM: raise Halt
        wb = self.worker_bonds.get(worker_id)
        if wb is not None and wb.state != W_GONE: raise Halt
        self.lock(("wbond", worker_id), stake)
        self.worker_bonds[worker_id] = WorkerBond(worker_id, stake, self.height)

    def worker_deregister(self, worker_id):
        wb = self.worker_bonds.get(worker_id)
        if wb is None: raise Halt
        if wb.state != W_REG: raise Halt              # slashed cannot deregister
        wb.state = W_DEREG
        wb.dereg_block = self.height

    def worker_reclaim(self, worker_id):
        wb = self.worker_bonds.get(worker_id)
        if wb is None: raise Halt
        if wb.state != W_DEREG: raise Halt            # slashed halts here forever
        if self.height <= wb.dereg_block + self.stake_cooldown: raise Halt
        if wb.encumbrances > 0: raise Halt            # closes the mid-dispute escape
        self.unlock(("wbond", worker_id), wb.stake)
        wb.stake = 0
        wb.state = W_GONE

    def slash_sweep(self, worker_id):
        # treasury pulls a slashed bond out of the contract. It must WAIT for
        # every remaining encumbered dispute on this bond to terminate
        # (encumbrances == 0). Fuzzer-found bug: sweeping early frees the
        # identity to re-bond while an old dispute still holds an encumbrance
        # on it, and that dispute's termination would then hit the fresh
        # innocent bond, wrongly slashing it or corrupting the counter.
        wb = self.worker_bonds.get(worker_id)
        if wb is None: raise Halt
        if wb.state != W_SLASHED: raise Halt
        if wb.encumbrances > 0: raise Halt
        self.unlock(("wbond", worker_id), wb.stake)
        wb.stake = 0
        wb.state = W_GONE

    # ------------------------------------------------- quorum dispute path

    def dispute(self, job_id):
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.mode != MODE_B: raise Halt
        if job.status != AWAITING: raise Halt
        if self.height > job.review_deadline: raise Halt
        self.lock(job_id, job.dispute_fee)
        job.dispute_filed = self.height
        job.frozen_n = self.n_registered             # N from the counter, no set
        job.threshold = (job.frozen_n // 2) + 1 if job.frozen_n else 1
        job.status = DISPUTED
        # v2: encumber the worker's live bond, if any. Only the bond that
        # exists when the dispute is filed is at risk from it.
        if job.worker is not None:
            wb = self.worker_bonds.get(job.worker)
            if wb is not None and wb.stake > 0 and wb.state in (W_REG, W_DEREG):
                wb.encumbrances += 1
                job.bond_encumbered = True

    def vote(self, arb_id, job_id, side):
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.mode != MODE_B: raise Halt
        if job.status != DISPUTED: raise Halt
        a = self.arbs.get(arb_id)
        if a is None or a.stake == 0: raise Halt
        if a.registered_at > job.dispute_filed: raise Halt   # only pre-dispute
        if arb_id in job.votes: raise Halt                   # one immutable vote
        if side not in (ALICE, BOB): raise Halt
        job.votes[arb_id] = side
        if side == ALICE:
            job.vc_alice += 1
            tally = job.vc_alice
        else:
            job.vc_bob += 1
            tally = job.vc_bob
        if tally >= job.threshold:
            self._resolve(job, side)

    def _resolve(self, job, winning_side):
        # exactly M voters backed the winning side; freeze the per-voter share
        m = job.threshold
        job.resolution = winning_side
        job.fee_share = job.dispute_fee // m
        job.fee_remainder = job.dispute_fee - job.fee_share * m
        job.status = R_ALICE if winning_side == ALICE else R_BOB
        # v2: settle the bond encumbrance. Resolution to Alice (worker lost
        # an arbitrated dispute) slashes the whole bond, at most once; to Bob
        # it releases the encumbrance untouched.
        if job.bond_encumbered:
            wb = self.worker_bonds[job.worker]
            wb.encumbrances -= 1
            job.bond_encumbered = False
            if winning_side == ALICE and wb.state in (W_REG, W_DEREG) and wb.stake > 0:
                wb.state = W_SLASHED

    def winner_claim(self, job_id):
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.resolution is None: raise Halt
        if job.winner_paid: raise Halt
        self.unlock(job_id, job.payment + job.collateral)    # winner: P + C only
        job.winner_paid = True

    def arb_reward_claim(self, arb_id, job_id):
        # one consensus voter claims its F // M share, its own transaction
        job = self.jobs.get(job_id)
        if job is None: raise Halt
        if job.resolution is None: raise Halt
        if job.votes.get(arb_id) != job.resolution: raise Halt   # must be consensus
        if arb_id in job.fee_claimed: raise Halt
        if job.fee_share > 0:
            self.unlock(job_id, job.fee_share)
        job.fee_claimed.add(arb_id)


# ------------------------------------------------------------------ checks

def check_conservation(chain):
    for unit, (locked, unlocked) in chain.ledger.items():
        if unlocked > locked:
            label = "?"
            if isinstance(unit, int) and unit in chain.jobs:
                label = STATUS_NAMES[chain.jobs[unit].status]
            elif isinstance(unit, tuple):
                label = unit[0]
            raise AssertionError(
                "CONSERVATION VIOLATED unit %s: unlocked %s > locked %s (%s)"
                % (unit, unlocked, locked, label))


def check_encumbrance(chain):
    # every bond's counter equals its live bond-encumbered disputes
    per = {}
    for j in chain.jobs.values():
        if j.bond_encumbered:
            if j.status != DISPUTED:
                raise AssertionError(
                    "ENCUMBRANCE flag outlived the dispute: job %s status %s"
                    % (j.job_id, STATUS_NAMES[j.status]))
            per[j.worker] = per.get(j.worker, 0) + 1
    for w, wb in chain.worker_bonds.items():
        expect = per.get(w, 0)
        if wb.encumbrances != expect:
            raise AssertionError(
                "ENCUMBRANCE VIOLATED worker %s: counter %s, live encumbered disputes %s"
                % (w, wb.encumbrances, expect))


def check_all(chain):
    check_conservation(chain)
    check_encumbrance(chain)


def drain_everything(chain):
    horizon = max([j.expiry for j in chain.jobs.values()] +
                  [j.review_deadline for j in chain.jobs.values()] +
                  [j.dispute_filed + chain.arbitrator_timeout for j in chain.jobs.values()] +
                  [a.dereg_block + chain.stake_cooldown for a in chain.arbs.values()] +
                  [wb.dereg_block + chain.stake_cooldown for wb in chain.worker_bonds.values()] +
                  [chain.height])
    chain.height = horizon + 2

    def run_jobs():
        progressed = True
        while progressed:
            progressed = False
            for job_id in list(chain.jobs):
                job = chain.jobs[job_id]
                for method in (chain.refund, chain.claim_after_timeout, chain.claim,
                               chain.winner_claim, chain.void_dispute,
                               chain.void_claim_requester, chain.void_claim_node,
                               chain.sweep):
                    try:
                        method(job_id)
                        progressed = True
                        check_all(chain)
                    except Halt:
                        pass
                # per-voter reward claims (stand in for independent txs)
                if job.resolution is not None:
                    for aid in list(job.votes):
                        try:
                            chain.arb_reward_claim(aid, job_id)
                            progressed = True
                            check_all(chain)
                        except Halt:
                            pass

    run_jobs()

    # every dispute is terminal now, so no bond may still be encumbered
    for w, wb in chain.worker_bonds.items():
        if wb.encumbrances != 0:
            raise AssertionError(
                "encumbrance survived terminal jobs: worker %s count %s"
                % (w, wb.encumbrances))

    # deregister every still-active identity on both registries
    for aid in list(chain.arbs):
        try:
            chain.deregister(aid)
        except Halt:
            pass
    for wid in list(chain.worker_bonds):
        try:
            chain.worker_deregister(wid)
        except Halt:
            pass
    chain.height += chain.stake_cooldown + 2
    # reclaim every arbitrator bond in full; drain every worker bond either
    # by reclaim (honest exit) or by slash_sweep (treasury takes a slash)
    for aid in list(chain.arbs):
        try:
            chain.reclaim_stake(aid)
            check_all(chain)
        except Halt:
            pass
    for wid in list(chain.worker_bonds):
        try:
            chain.worker_reclaim(wid)
            check_all(chain)
        except Halt:
            pass
        try:
            chain.slash_sweep(wid)
            check_all(chain)
        except Halt:
            pass

    stuck = []
    for unit, (locked, unlocked) in chain.ledger.items():
        if locked != unlocked:
            if isinstance(unit, int):
                label = STATUS_NAMES[chain.jobs[unit].status]
            elif unit[0] == "arb":
                label = "arb:%s" % (chain.arbs[unit[1]].state,)
            else:
                label = "wbond:%s" % (chain.worker_bonds[unit[1]].state,)
            stuck.append((unit, locked, unlocked, label))
    return stuck


# ------------------------------------------------------------------- fuzzer

def fuzz_sequence(seed, n_calls=600, n_jobs=8, n_arbs=8, n_workers=4):
    rng = random.Random(seed)
    chain = Chain(arbitrator_timeout=rng.choice([1, 5, 50]),
                  default_review_window=rng.choice([5, 50]),
                  stake_cooldown=rng.choice([1, 5, 20]))
    job_ids = list(range(1, n_jobs + 1))
    arb_pool = list(range(101, 101 + n_arbs))
    worker_pool = list(range(501, 501 + n_workers))
    amounts = [1, 2, 100, 100000]          # 1 and 2 sit below MIN_ARB_BOND
    assets = [BEAM, BEAM, BEAM, 47]        # 47 exercises the BEAM-only gate

    def rand_worker():
        # None = fresh key per job, carries no bond
        return rng.choice(worker_pool + [None, None])

    def rand_call():
        op = rng.randrange(25)
        if op == 0:
            chain.create_a(rng.choice(job_ids), rng.choice(amounts),
                           chain.height + rng.choice([1, 2, 5, 30]), result_hash=7,
                           required_collateral=rng.choice([0, 0, 2, 100]),
                           spec_hash=rng.randrange(1000), worker=rand_worker())
        elif op == 1:
            chain.create_b(rng.choice(job_ids), rng.choice(amounts), rng.choice(amounts),
                           chain.height + rng.choice([1, 2, 5, 30]),
                           rng.choice([0, 1, 3, 10]),
                           required_collateral=rng.choice([0, 0, 2, 100]),
                           spec_hash=rng.randrange(1000), worker=rand_worker())
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
            res = [j for j in chain.jobs.values() if j.resolution is not None and j.votes]
            if res:
                job = rng.choice(res)
                chain.arb_reward_claim(rng.choice(list(job.votes)), job.job_id)
            else:
                chain.arb_reward_claim(rng.choice(arb_pool), rng.choice(job_ids))
        elif op == 15:
            # exercises the floor, the asset gate, the admin gate, and gone
            # identity reuse, all through the same call
            chain.register(rng.choice(arb_pool), rng.choice(amounts),
                           asset=rng.choice(assets),
                           admin=rng.random() > 0.15)
        elif op == 16:
            rng.choice([chain.deregister, chain.reclaim_stake])(rng.choice(arb_pool))
        elif op == 17:
            disputed = [j for j in chain.jobs.values() if j.status == DISPUTED]
            if disputed:
                job = rng.choice(disputed)
                # mostly aim an eligible fresh voter with a majority-side bias
                # so random walks actually reach quorum (and live slashes);
                # the rest stay blind so the halt paths keep getting exercised
                eligible = [aid for aid, a in chain.arbs.items()
                            if a.stake > 0 and a.registered_at <= job.dispute_filed
                            and aid not in job.votes]
                if eligible and rng.random() < 0.8:
                    aid = rng.choice(eligible)
                else:
                    aid = rng.choice(arb_pool)
                side = ALICE if rng.random() < 0.65 else BOB
                chain.vote(aid, job.job_id, side)
            else:
                chain.vote(rng.choice(arb_pool), rng.choice(job_ids), rng.choice([ALICE, BOB]))
        elif op == 18:
            chain.worker_register(rng.choice(worker_pool), rng.choice(amounts),
                                  asset=rng.choice(assets))
        elif op == 19:
            rng.choice([chain.worker_deregister, chain.worker_reclaim])(rng.choice(worker_pool))
        elif op == 20:
            chain.slash_sweep(rng.choice(worker_pool))
        # ---- guided ops. The blind ops above keep every halt path hot, but
        # on their own they almost never complete the create -> commit ->
        # submit -> dispute pipeline (short expiries burn the job ids), so
        # quorum resolution and the slash would go untested against random
        # interleavings. These aim valid arguments at real jobs; the MODEL is
        # untouched, only the driver is smarter.
        elif op == 21:
            # long-lived Mode B job, usually on a bondable worker identity
            free = [j for j in job_ids if j not in chain.jobs]
            if not free: raise Halt
            chain.create_b(rng.choice(free), rng.choice(amounts), rng.choice(amounts),
                           chain.height + rng.choice([50, 100, 300]),
                           rng.choice([0, 10, 20]),
                           worker=rng.choice(worker_pool))
        elif op == 22:
            opens = [j for j in chain.jobs.values() if j.status == OPEN]
            if not opens: raise Halt
            job = rng.choice(opens)
            chain.commit(job.job_id, max(job.required_collateral, rng.choice(amounts)))
        elif op == 23:
            active = [j for j in chain.jobs.values() if j.status == ACTIVE]
            if not active: raise Halt
            job = rng.choice(active)
            chain.submit_delivery(job.job_id, job.result_hash if job.mode == MODE_A else 7)
        elif op == 24:
            awaiting = [j for j in chain.jobs.values()
                        if j.status == AWAITING and j.mode == MODE_B]
            if not awaiting: raise Halt
            chain.dispute(rng.choice(awaiting).job_id)

    successes = 0
    status_seen = set()
    slashes_seen = [0]
    for _ in range(n_calls):
        if rng.random() < 0.35:
            chain.height += rng.choice([1, 1, 2, 7])
        try:
            rand_call()
            successes += 1
            check_all(chain)
        except Halt:
            pass
        for j in chain.jobs.values():
            status_seen.add(j.status)
        slashes_seen[0] += sum(1 for wb in chain.worker_bonds.values()
                               if wb.state == W_SLASHED)

    stuck = drain_everything(chain)
    if stuck:
        raise AssertionError("LIVENESS VIOLATED seed %s, stuck units: %s" % (seed, stuck))
    return successes, status_seen, slashes_seen[0] > 0


def fuzz_mofn_scenario(seed, counters):
    """v1 regression, unchanged in intent: force the quorum path. Register
    arbs (now with valid v2 stakes), drive a Mode B job to a filed dispute,
    then deterministically reach a quorum or a deliberate split. Assert the
    expected terminal, the per-voter reward split, and full drain."""
    rng = random.Random(seed)
    chain = Chain(arbitrator_timeout=rng.choice([20, 50, 100]),
                  default_review_window=rng.choice([5, 50]),
                  stake_cooldown=rng.choice([1, 5, 20]))
    amounts = [1, 2, 100, 100000]
    valid_stakes = [s for s in amounts if s >= MIN_ARB_BOND]

    n = rng.randint(1, 6)
    arb_ids = list(range(101, 101 + n))
    stakes = {}
    for aid in arb_ids:
        s = rng.choice(valid_stakes)
        chain.register(aid, s)
        stakes[aid] = s

    jid = 1
    payment = rng.choice(amounts)
    fee = rng.choice(amounts)
    chain.create_b(jid, payment, fee, chain.height + 200, rng.choice([0, 5, 10]))
    chain.commit(jid, rng.choice(amounts))
    chain.submit_delivery(jid, rng.choice([7, 9]))    # Mode B ignores the hash
    chain.dispute(jid)
    job = chain.jobs[jid]
    M = job.threshold
    assert job.frozen_n == n and M == (n // 2) + 1
    snap = sorted(arb_ids)

    outcome = rng.choice(["alice", "bob", "split"])

    if outcome in ("alice", "bob"):
        win = ALICE if outcome == "alice" else BOB
        lose = BOB if win == ALICE else ALICE
        rng.shuffle(snap)
        winners = snap[:M]
        pre_losers = snap[M:][:max(M - 1, 0)]   # below quorum, just earn nothing
        for a in pre_losers:
            chain.vote(a, jid, lose)
        assert job.status == DISPUTED, (seed, "premature resolve")
        for a in winners:
            chain.vote(a, jid, win)
        assert job.status == (R_ALICE if win == ALICE else R_BOB), \
            (seed, "expected resolve", job.status, M, n)
        for a in pre_losers:
            locked, unlocked = chain.ledger[("arb", a)]
            assert unlocked == 0 and chain.arbs[a].stake == stakes[a], \
                (seed, "loser bond touched", a)
        counters["resolved_" + win] += 1
        counters["consensus_voters"] += M
    else:
        rng.shuffle(snap)
        half = M - 1
        for a in snap[:half]:
            chain.vote(a, jid, ALICE)
        for a in snap[half:half + half]:
            chain.vote(a, jid, BOB)
        assert job.status == DISPUTED, (seed, "split unexpectedly resolved")
        counters["split"] += 1

    stuck = drain_everything(chain)
    if stuck:
        raise AssertionError("SCENARIO LIVENESS VIOLATED seed %s: %s" % (seed, stuck))

    if outcome in ("alice", "bob"):
        assert job.winner_paid, (seed, "winner not paid")
        paid_shares = len(job.fee_claimed) * job.fee_share
        assert paid_shares + (job.fee_remainder if job.remainder_swept else 0) == fee, \
            (seed, "fee not fully distributed", paid_shares, job.fee_remainder, fee)
        locked, unlocked = chain.ledger[jid]
        assert locked == unlocked == payment + chain_committed(chain, job) + fee, \
            (seed, "job not fully drained")
    else:
        assert job.status == VOIDED, (seed, "split not voided", STATUS_NAMES[job.status])
    for aid in arb_ids:
        locked, unlocked = chain.ledger[("arb", aid)]
        assert locked == unlocked == stakes[aid], (seed, "bond not drained", aid)


def fuzz_eligibility_attack(seed, counters):
    """v1 regression: after a dispute freezes N and M, a post-dispute
    registration flood must not vote, must not move the freeze or the tally,
    and the legitimate pre-dispute set must still resolve."""
    rng = random.Random(seed)
    chain = Chain(arbitrator_timeout=rng.choice([20, 50, 100]),
                  default_review_window=rng.choice([5, 50]),
                  stake_cooldown=rng.choice([1, 5, 20]))
    amounts = [1, 2, 100, 100000]
    valid_stakes = [s for s in amounts if s >= MIN_ARB_BOND]

    k = rng.randint(1, 5)
    pre = list(range(101, 101 + k))
    stakes = {}
    for aid in pre:
        s = rng.choice(valid_stakes)
        chain.register(aid, s)
        stakes[aid] = s

    jid = 1
    payment = rng.choice(amounts)
    fee = rng.choice(amounts)
    chain.create_b(jid, payment, fee, chain.height + 300, rng.choice([0, 5, 10]))
    chain.commit(jid, rng.choice(amounts))
    chain.submit_delivery(jid, rng.choice([7, 9]))
    chain.dispute(jid)
    job = chain.jobs[jid]
    frozen_n, M = job.frozen_n, job.threshold
    assert frozen_n == k and M == (k // 2) + 1, (seed, "wrong freeze", frozen_n, M, k)

    chain.height += rng.choice([1, 2, 5])

    flood = list(range(201, 201 + rng.randint(1, 8)))
    fstakes = {}
    for aid in flood:
        s = rng.choice(valid_stakes)
        chain.register(aid, s)
        fstakes[aid] = s

    assert job.frozen_n == frozen_n and job.threshold == M, (seed, "freeze moved under flood")
    assert chain.n_registered == k + len(flood), (seed, "counter wrong")

    for aid in flood:
        try:
            chain.vote(aid, jid, rng.choice([ALICE, BOB]))
            raise AssertionError((seed, "post-dispute registration voted", aid))
        except Halt:
            pass

    assert job.vc_alice == 0 and job.vc_bob == 0, (seed, "flood moved the tally")
    counters["floods_blocked"] += len(flood)

    rng.shuffle(pre)
    win = rng.choice([ALICE, BOB])
    for aid in pre[:M]:
        chain.vote(aid, jid, win)
    assert job.resolution is not None, (seed, "legit quorum failed after flood")
    counters["resolved_after_flood"] += 1

    stuck = drain_everything(chain)
    if stuck:
        raise AssertionError("ELIGIBILITY LIVENESS VIOLATED seed %s: %s" % (seed, stuck))
    for aid in pre:
        locked, unlocked = chain.ledger[("arb", aid)]
        assert locked == unlocked == stakes[aid], (seed, "pre bond not drained", aid)
    for aid in flood:
        locked, unlocked = chain.ledger[("arb", aid)]
        assert locked == unlocked == fstakes[aid], (seed, "flood bond not drained", aid)


def fuzz_registry_hardening(seed, counters):
    """v2: the register gates and identity reuse. Below-floor, wrong-asset,
    and no-admin registrations must all halt cleanly. A gone identity may
    re-register, and the re-registration must not regain eligibility on a
    dispute filed during its previous life."""
    rng = random.Random(seed)
    chain = Chain(arbitrator_timeout=rng.choice([20, 50, 100]),
                  default_review_window=rng.choice([5, 50]),
                  stake_cooldown=rng.choice([1, 5]))
    amounts = [1, 2, 100, 100000]
    valid_stakes = [s for s in amounts if s >= MIN_ARB_BOND]

    # the three gates, each alone must halt with no state change
    for kwargs in ({"stake": MIN_ARB_BOND - 1},
                   {"stake": rng.choice(valid_stakes), "asset": 47},
                   {"stake": rng.choice(valid_stakes), "admin": False}):
        try:
            chain.register(999, **kwargs)
            raise AssertionError((seed, "gate failed", kwargs))
        except Halt:
            pass
        assert 999 not in chain.arbs and chain.n_registered == 0, \
            (seed, "gate leaked state", kwargs)
    counters["gates_held"] += 3

    # honest quorum base plus the identity we will churn
    k = rng.randint(2, 4)
    steady = list(range(101, 101 + k))
    for aid in steady:
        chain.register(aid, rng.choice(valid_stakes))
    churn = 200
    churn_stake = rng.choice(valid_stakes)
    chain.register(churn, churn_stake)

    # a live identity cannot double register
    try:
        chain.register(churn, rng.choice(valid_stakes))
        raise AssertionError((seed, "double register"))
    except Halt:
        pass

    # dispute filed while churn is registered and eligible
    jid = 1
    chain.create_b(jid, rng.choice(amounts), rng.choice(amounts),
                   chain.height + 300, 5)
    chain.commit(jid, rng.choice(amounts))
    chain.submit_delivery(jid, 7)
    chain.dispute(jid)
    job = chain.jobs[jid]
    n_at_freeze = job.frozen_n
    assert n_at_freeze == k + 1

    # churn exits fully mid dispute (allowed, it only removes a voter)
    chain.deregister(churn)
    chain.height += chain.stake_cooldown + 1
    chain.reclaim_stake(churn)
    assert chain.arbs[churn].state == A_GONE
    locked, unlocked = chain.ledger[("arb", churn)]
    assert locked == unlocked == churn_stake, (seed, "churn bond not whole")

    # v2: the gone identity re-registers, fresh registered_at
    reborn_stake = rng.choice(valid_stakes)
    chain.register(churn, reborn_stake)
    assert chain.arbs[churn].state == A_REG
    assert chain.arbs[churn].registered_at == chain.height
    counters["reborn"] += 1

    # the freeze never moved, and the reborn identity is not eligible on the
    # dispute from its previous life
    assert job.frozen_n == n_at_freeze and job.status == DISPUTED
    try:
        chain.vote(churn, jid, rng.choice([ALICE, BOB]))
        raise AssertionError((seed, "reborn identity regained eligibility"))
    except Halt:
        pass
    counters["rebirth_votes_blocked"] += 1

    # the steady pre-dispute set still resolves
    win = rng.choice([ALICE, BOB])
    for aid in steady[:job.threshold]:
        chain.vote(aid, jid, win)
    assert job.resolution is not None, (seed, "steady quorum failed")

    stuck = drain_everything(chain)
    if stuck:
        raise AssertionError("HARDENING LIVENESS VIOLATED seed %s: %s" % (seed, stuck))


def fuzz_worker_slash(seed, counters):
    """v2 core: a bonded worker through each dispute outcome. Resolve to
    Alice slashes the whole bond to the treasury; resolve to Bob and void
    both leave it untouched and reclaimable in full."""
    rng = random.Random(seed)
    chain = Chain(arbitrator_timeout=rng.choice([20, 50, 100]),
                  default_review_window=rng.choice([5, 50]),
                  stake_cooldown=rng.choice([1, 5, 20]))
    amounts = [1, 2, 100, 100000]
    valid_stakes = [s for s in amounts if s >= MIN_ARB_BOND]

    n = rng.randint(1, 5)
    arb_ids = list(range(101, 101 + n))
    for aid in arb_ids:
        chain.register(aid, rng.choice(valid_stakes))

    W = 501
    bond = rng.choice(amounts)          # worker bond has no floor
    chain.worker_register(W, bond)
    # BEAM only holds on the worker side too
    try:
        chain.worker_register(502, rng.choice(amounts), asset=47)
        raise AssertionError((seed, "worker bond wrong asset accepted"))
    except Halt:
        pass

    jid = 1
    payment = rng.choice(amounts)
    fee = rng.choice(amounts)
    chain.create_b(jid, payment, fee, chain.height + 300, rng.choice([0, 5, 10]),
                   worker=W)
    chain.commit(jid, rng.choice(amounts))
    chain.submit_delivery(jid, rng.choice([7, 9]))
    chain.dispute(jid)
    job = chain.jobs[jid]
    wb = chain.worker_bonds[W]
    assert job.bond_encumbered and wb.encumbrances == 1, (seed, "not encumbered")
    M = job.threshold

    outcome = rng.choice(["alice", "bob", "void"])
    snap = list(arb_ids)
    rng.shuffle(snap)

    if outcome == "alice":
        for aid in snap[:M]:
            chain.vote(aid, jid, ALICE)
        assert job.status == R_ALICE
        assert wb.state == W_SLASHED and wb.encumbrances == 0, (seed, "no slash")
        assert wb.stake == bond, (seed, "slashed stake moved early")
        # the slashed bond is unreachable by the worker, on every path
        try:
            chain.worker_deregister(W)
            raise AssertionError((seed, "slashed bond deregistered"))
        except Halt:
            pass
        try:
            chain.worker_reclaim(W)
            raise AssertionError((seed, "slashed bond reclaimed"))
        except Halt:
            pass
        # treasury takes it, exactly once
        chain.slash_sweep(W)
        locked, unlocked = chain.ledger[("wbond", W)]
        assert locked == unlocked == bond, (seed, "slash sweep wrong amount")
        try:
            chain.slash_sweep(W)
            raise AssertionError((seed, "double slash sweep"))
        except Halt:
            pass
        # the identity is gone and may bond again from scratch
        rebond = rng.choice(amounts)
        chain.worker_register(W, rebond)
        assert chain.worker_bonds[W].state == W_REG
        counters["slashes"] += 1
    elif outcome == "bob":
        for aid in snap[:M]:
            chain.vote(aid, jid, BOB)
        assert job.status == R_BOB
        assert wb.state == W_REG and wb.encumbrances == 0, (seed, "bob touched bond")
        assert wb.stake == bond
        counters["bob_survivals"] += 1
    else:
        # nobody votes; past the arbitrator timeout the dispute voids and
        # the encumbrance releases
        chain.height += chain.arbitrator_timeout + 1
        chain.void_dispute(jid)
        assert wb.state == W_REG and wb.encumbrances == 0, (seed, "void kept encumbrance")
        assert wb.stake == bond
        counters["void_releases"] += 1

    stuck = drain_everything(chain)
    if stuck:
        raise AssertionError("SLASH LIVENESS VIOLATED seed %s: %s" % (seed, stuck))
    locked, unlocked = chain.ledger[("wbond", W)]
    assert locked == unlocked, (seed, "worker bond unit not drained")


def fuzz_slash_escape(seed, counters):
    """v2: the escape the encumbrance exists to close. A bonded worker whose
    job is disputed deregisters immediately and waits out the cooldown; the
    reclaim must still halt while the dispute is open. If the dispute then
    resolves against them the bond is slashed anyway; if it resolves for
    them or voids, the reclaim goes through in full. Also the by-design
    non-case: a bond registered after the dispute was filed is not at risk
    from it."""
    rng = random.Random(seed)
    chain = Chain(arbitrator_timeout=rng.choice([20, 50, 100]),
                  default_review_window=rng.choice([5, 50]),
                  stake_cooldown=rng.choice([1, 5]))
    amounts = [1, 2, 100, 100000]
    valid_stakes = [s for s in amounts if s >= MIN_ARB_BOND]

    n = rng.randint(1, 4)
    arb_ids = list(range(101, 101 + n))
    for aid in arb_ids:
        chain.register(aid, rng.choice(valid_stakes))

    W = 501
    bond = rng.choice(amounts)
    chain.worker_register(W, bond)

    jid = 1
    chain.create_b(jid, rng.choice(amounts), rng.choice(amounts),
                   chain.height + 400, rng.choice([0, 5, 10]), worker=W)
    chain.commit(jid, rng.choice(amounts))
    chain.submit_delivery(jid, rng.choice([7, 9]))
    chain.dispute(jid)
    job = chain.jobs[jid]
    wb = chain.worker_bonds[W]
    assert wb.encumbrances == 1

    # the escape attempt: deregister, wait past the cooldown, try to pull the
    # bond while the ruling is pending
    chain.worker_deregister(W)
    chain.height += chain.stake_cooldown + 1
    assert job.status == DISPUTED, (seed, "dispute fell over early")
    try:
        chain.worker_reclaim(W)
        raise AssertionError((seed, "ESCAPE: bond reclaimed mid dispute"))
    except Halt:
        pass
    counters["escapes_blocked"] += 1

    outcome = rng.choice(["alice", "bob"])
    M = job.threshold
    snap = list(arb_ids)
    rng.shuffle(snap)
    for aid in snap[:M]:
        chain.vote(aid, jid, ALICE if outcome == "alice" else BOB)

    if outcome == "alice":
        # deregistering did not dodge the slash
        assert wb.state == W_SLASHED, (seed, "dereg dodged the slash")
        try:
            chain.worker_reclaim(W)
            raise AssertionError((seed, "slashed bond reclaimed"))
        except Halt:
            pass
        chain.slash_sweep(W)
        counters["escape_slashes"] += 1
    else:
        # honest outcome: encumbrance released, the pending reclaim now works
        assert wb.encumbrances == 0 and wb.state == W_DEREG
        chain.worker_reclaim(W)
        locked, unlocked = chain.ledger[("wbond", W)]
        assert locked == unlocked == bond, (seed, "post-bob reclaim short")
        counters["escape_survivals"] += 1

    # by design: a bond registered after the dispute was filed is untouched
    L = 502
    jid2 = 2
    chain.create_b(jid2, rng.choice(amounts), rng.choice(amounts),
                   chain.height + 400, rng.choice([0, 5, 10]), worker=L)
    chain.commit(jid2, rng.choice(amounts))
    chain.submit_delivery(jid2, rng.choice([7, 9]))
    chain.dispute(jid2)
    late_bond = rng.choice(amounts)
    chain.worker_register(L, late_bond)          # bonded after filing
    job2 = chain.jobs[jid2]
    wb2 = chain.worker_bonds[L]
    assert not job2.bond_encumbered and wb2.encumbrances == 0, (seed, "late bond encumbered")
    M2 = job2.threshold
    rng.shuffle(snap)
    for aid in snap[:M2]:
        chain.vote(aid, jid2, ALICE)             # worker loses
    assert wb2.state == W_REG and wb2.stake == late_bond, (seed, "late bond slashed")
    counters["late_bonds_untouched"] += 1

    stuck = drain_everything(chain)
    if stuck:
        raise AssertionError("ESCAPE LIVENESS VIOLATED seed %s: %s" % (seed, stuck))


def fuzz_double_slash(seed, counters):
    """v2: one bond, several encumbered disputes. The first resolution to
    Alice slashes the whole bond; every later resolution only releases its
    encumbrance. Exactly one sweep for the one bond, conservation holds."""
    rng = random.Random(seed)
    chain = Chain(arbitrator_timeout=rng.choice([20, 50, 100]),
                  default_review_window=rng.choice([5, 50]),
                  stake_cooldown=rng.choice([1, 5]))
    amounts = [1, 2, 100, 100000]
    valid_stakes = [s for s in amounts if s >= MIN_ARB_BOND]

    n = rng.randint(1, 4)
    arb_ids = list(range(101, 101 + n))
    for aid in arb_ids:
        chain.register(aid, rng.choice(valid_stakes))

    W = 501
    bond = rng.choice(amounts)
    chain.worker_register(W, bond)
    wb = chain.worker_bonds[W]

    n_disputes = rng.randint(2, 4)
    jids = list(range(1, 1 + n_disputes))
    for jid in jids:
        chain.create_b(jid, rng.choice(amounts), rng.choice(amounts),
                       chain.height + 500, rng.choice([0, 5, 10]), worker=W)
        chain.commit(jid, rng.choice(amounts))
        chain.submit_delivery(jid, rng.choice([7, 9]))
        chain.dispute(jid)
    assert wb.encumbrances == n_disputes, (seed, "encumbrance count wrong")

    # resolve them all to Alice, in random order
    rng.shuffle(jids)
    slashed_at = None
    for i, jid in enumerate(jids):
        job = chain.jobs[jid]
        M = job.threshold
        snap = list(arb_ids)
        rng.shuffle(snap)
        for aid in snap[:M]:
            chain.vote(aid, jid, ALICE)
        assert job.status == R_ALICE
        if slashed_at is None:
            slashed_at = i
        assert wb.state == W_SLASHED, (seed, "slash state lost", i)
        assert wb.stake == bond, (seed, "stake moved on repeat slash", i)
        assert wb.encumbrances == n_disputes - (i + 1), (seed, "release wrong", i)

    assert wb.encumbrances == 0
    chain.slash_sweep(W)
    locked, unlocked = chain.ledger[("wbond", W)]
    assert locked == unlocked == bond, (seed, "sweep total wrong")
    try:
        chain.slash_sweep(W)
        raise AssertionError((seed, "double sweep"))
    except Halt:
        pass
    counters["multi_dispute_single_slash"] += 1

    stuck = drain_everything(chain)
    if stuck:
        raise AssertionError("DOUBLE SLASH LIVENESS VIOLATED seed %s: %s" % (seed, stuck))


def fuzz_sweep_rebond_attack(seed, counters):
    """v2 regression for the fuzzer-found bug: with two encumbered disputes
    on one bond, slashing on the first must NOT let the treasury sweep (and
    the identity re-bond) while the second is still open, because the second
    dispute's termination would then hit a fresh innocent bond. The sweep
    waits for encumbrances == 0; this scenario is that rule's regression
    test."""
    rng = random.Random(seed)
    chain = Chain(arbitrator_timeout=rng.choice([20, 50, 100]),
                  default_review_window=rng.choice([5, 50]),
                  stake_cooldown=rng.choice([1, 5]))
    amounts = [1, 2, 100, 100000]
    valid_stakes = [s for s in amounts if s >= MIN_ARB_BOND]

    n = rng.randint(1, 4)
    arb_ids = list(range(101, 101 + n))
    for aid in arb_ids:
        chain.register(aid, rng.choice(valid_stakes))

    W = 501
    bond = rng.choice(amounts)
    chain.worker_register(W, bond)
    wb = chain.worker_bonds[W]

    for jid in (1, 2):
        chain.create_b(jid, rng.choice(amounts), rng.choice(amounts),
                       chain.height + 500, rng.choice([0, 5, 10]), worker=W)
        chain.commit(jid, rng.choice(amounts))
        chain.submit_delivery(jid, rng.choice([7, 9]))
        chain.dispute(jid)
    assert wb.encumbrances == 2

    def quorum(jid, side):
        job = chain.jobs[jid]
        snap = list(arb_ids)
        rng.shuffle(snap)
        for aid in snap[:job.threshold]:
            chain.vote(aid, jid, side)

    # first dispute slashes the bond; the second is still open
    quorum(1, ALICE)
    assert wb.state == W_SLASHED and wb.encumbrances == 1

    # the attack window: sweeping now, then re-bonding, would leave dispute 2
    # pointing at a fresh bond. Both doors must be shut.
    try:
        chain.slash_sweep(W)
        raise AssertionError((seed, "SWEEP with a live encumbrance"))
    except Halt:
        pass
    try:
        chain.worker_register(W, rng.choice(amounts))
        raise AssertionError((seed, "re-bonded a slashed identity"))
    except Halt:
        pass
    counters["early_sweeps_blocked"] += 1

    # the second dispute terminates either way; only then does the sweep go
    second = rng.choice(["alice", "bob", "void"])
    if second == "void":
        chain.height += chain.arbitrator_timeout + 1
        chain.void_dispute(2)
    else:
        quorum(2, ALICE if second == "alice" else BOB)
    assert wb.state == W_SLASHED and wb.encumbrances == 0
    assert wb.stake == bond, (seed, "stake moved before sweep")

    chain.slash_sweep(W)
    locked, unlocked = chain.ledger[("wbond", W)]
    assert locked == unlocked == bond, (seed, "sweep amount wrong")

    # the identity may bond again, fresh and unencumbered
    chain.worker_register(W, rng.choice(amounts))
    wb2 = chain.worker_bonds[W]
    assert wb2.state == W_REG and wb2.encumbrances == 0
    counters["clean_rebonds"] += 1

    stuck = drain_everything(chain)
    if stuck:
        raise AssertionError("SWEEP REBOND LIVENESS VIOLATED seed %s: %s" % (seed, stuck))


def chain_committed(chain, job):
    # collateral that was locked for this job (commit happened in scenario)
    locked, _ = chain.ledger[job.job_id]
    return locked - job.payment - job.dispute_fee


def main():
    n_seq = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    base = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    total_success = 0
    all_statuses = set()
    seq_with_slash = 0
    for i in range(n_seq):
        s, seen, slashed = fuzz_sequence(base + i)
        total_success += s
        all_statuses |= seen
        seq_with_slash += 1 if slashed else 0
    print("OK: %d sequences, %d successful calls, %d sequences hit a live slash. "
          "conservation, liveness, encumbrance integrity held."
          % (n_seq, total_success, seq_with_slash))
    print("Statuses reached: %s" % sorted(STATUS_NAMES[s] for s in all_statuses))
    missing = set(range(11)) - all_statuses
    if missing:
        print("WARNING statuses never reached: %s (weak coverage)"
              % sorted(STATUS_NAMES[s] for s in missing))

    counters = {"resolved_alice": 0, "resolved_bob": 0, "consensus_voters": 0, "split": 0}
    for i in range(n_seq):
        fuzz_mofn_scenario(base + i, counters)
    print("M of N scenarios: %d, resolved Alice %d, resolved Bob %d, "
          "consensus voters paid %d, split-to-void %d."
          % (n_seq, counters["resolved_alice"], counters["resolved_bob"],
             counters["consensus_voters"], counters["split"]))

    elig = {"floods_blocked": 0, "resolved_after_flood": 0}
    for i in range(n_seq):
        fuzz_eligibility_attack(base + i, elig)
    print("Eligibility attack scenarios: %d, post-dispute registrations blocked %d, "
          "disputes still resolved by the frozen set %d."
          % (n_seq, elig["floods_blocked"], elig["resolved_after_flood"]))

    hard = {"gates_held": 0, "reborn": 0, "rebirth_votes_blocked": 0}
    for i in range(n_seq):
        fuzz_registry_hardening(base + i, hard)
    print("Registry hardening scenarios: %d, gate rejections %d, gone identities "
          "re-registered %d, rebirth votes blocked %d."
          % (n_seq, hard["gates_held"], hard["reborn"], hard["rebirth_votes_blocked"]))

    slash = {"slashes": 0, "bob_survivals": 0, "void_releases": 0}
    for i in range(n_seq):
        fuzz_worker_slash(base + i, slash)
    print("Worker slash scenarios: %d, bonds slashed and swept %d, survived a Bob "
          "resolution %d, released by void %d."
          % (n_seq, slash["slashes"], slash["bob_survivals"], slash["void_releases"]))

    esc = {"escapes_blocked": 0, "escape_slashes": 0, "escape_survivals": 0,
           "late_bonds_untouched": 0}
    for i in range(n_seq):
        fuzz_slash_escape(base + i, esc)
    print("Slash escape scenarios: %d, mid-dispute reclaims blocked %d, of which "
          "slashed anyway %d, survived honestly %d, late bonds untouched %d."
          % (n_seq, esc["escapes_blocked"], esc["escape_slashes"],
             esc["escape_survivals"], esc["late_bonds_untouched"]))

    dbl = {"multi_dispute_single_slash": 0}
    for i in range(n_seq):
        fuzz_double_slash(base + i, dbl)
    print("Double slash scenarios: %d, multi-dispute single-slash held %d times."
          % (n_seq, dbl["multi_dispute_single_slash"]))

    swp = {"early_sweeps_blocked": 0, "clean_rebonds": 0}
    for i in range(n_seq):
        fuzz_sweep_rebond_attack(base + i, swp)
    print("Sweep rebond scenarios: %d, early sweeps blocked %d, clean rebonds "
          "after full termination %d."
          % (n_seq, swp["early_sweeps_blocked"], swp["clean_rebonds"]))


if __name__ == "__main__":
    main()

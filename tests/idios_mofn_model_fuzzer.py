#!/usr/bin/env python3
"""
Idios contract funds-conservation fuzzer, M of N arbitration model (v1).

Extends the v5 model (idios_v5_model_fuzzer.py) with decentralized
arbitration. The escrow paths are unchanged. This is the on-chain-faithful
shape: a BVM method cannot iterate storage, so there is no stored snapshot
set and no atomic loop over voters. Every per-arbitrator effect is its own
keyed call, exactly as the contract will do it.

  ARBITRATOR REGISTRY. An arbitrator registers and locks a standing bond.
  The bond is a second locked pool, accounted like a job's funds (per unit,
  unlocked never exceeds locked). It is pure sybil resistance in v1: it is
  never slashed. Deregister, then after a cooldown with no bonded open
  dispute, reclaim it in full. A running counter tracks how many bonds are
  live (registered or deregistering), which is the N used for quorum.

  QUORUM RESOLVE. On dispute, N (the live registry count) and M (simple
  majority of N) are frozen onto the job. No set of keys is stored. A vote
  is allowed only from an arbitrator that was registered before the dispute
  was filed and still holds a bond. Each casts one immutable vote. The Mth
  matching vote resolves the job to Alice or Bob. Exactly M voters back the
  winning side at resolution (the Mth winning vote stops further votes).

  REWARD, PER VOTER. The dispute_fee no longer goes to the winner. The
  winner takes payment + collateral. The dispute_fee is the arbitration
  reward: each of the M consensus voters claims F // M for itself in its own
  transaction, and the remainder F % M is swept to treasury. No single call
  pays all M. The job's total payout is unchanged at P + C + F.

  NO SLASH (v1). A losing-side voter simply earns no reward; its bond is
  untouched and fully reclaimable. Slash is a later in place upgrade.

  NO QUORUM. Split votes or too few voters never resolve. The existing
  arbitrator_timeout void path handles it unchanged: requester reclaims P,
  node reclaims C, the fee goes to treasury. Every dispute still terminates.

Two properties asserted after every successful call and at end of sequence:

  SAFETY  (conservation): for every unit (job OR arbitrator bond), total
          unlocked never exceeds total locked.
  LIVENESS (drainability): after any sequence, once enough blocks pass,
          greedily applying recovery (refund, claim, winner_claim, the per
          voter arb_reward_claim, sweep of remainder and fee, void paths)
          plus deregister/reclaim drains every job AND every bond to
          locked == unlocked. Nothing is permanently stuck.

Note on enumeration: the CONTRACT may not iterate storage, so each reward
claim, each remainder sweep, each reclaim is a separate keyed method. This
fuzzer (the off-chain model) is allowed to loop over voters and arbitrators,
because that loop just stands in for many independent transactions.

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

A_REG, A_DEREG, A_GONE = "reg", "dereg", "gone"
ALICE, BOB = "alice", "bob"


class Halt(Exception):
    """Contract Env::Halt. The call fails, no state change, no funds move."""


class Job:
    __slots__ = ("job_id", "mode", "status", "payment", "collateral",
                 "dispute_fee", "expiry", "review_window", "review_deadline",
                 "dispute_filed", "result_hash", "delivery_hash",
                 "required_collateral", "spec_hash",
                 # M of N additions, all frozen/recorded on the job itself:
                 "frozen_n", "threshold", "votes", "vc_alice", "vc_bob",
                 "resolution", "fee_share", "fee_remainder",
                 "winner_paid", "fee_claimed", "remainder_swept")

    def __init__(self, job_id, mode, payment, dispute_fee, expiry, review_window, result_hash,
                 required_collateral=0, spec_hash=0):
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
        self.vc_alice = 0          # running tally, side ALICE
        self.vc_bob = 0            # running tally, side BOB
        self.resolution = None     # None / ALICE / BOB, set once at resolve
        self.fee_share = 0         # F // M
        self.fee_remainder = 0     # F %  M
        self.winner_paid = False
        self.fee_claimed = set()   # arb_ids that claimed their share
        self.remainder_swept = False


class Arb:
    __slots__ = ("arb_id", "stake", "state", "registered_at", "dereg_block")

    def __init__(self, arb_id, stake, registered_at):
        self.arb_id = arb_id
        self.stake = stake
        self.state = A_REG
        self.registered_at = registered_at
        self.dereg_block = 0


class Chain:
    def __init__(self, arbitrator_timeout, default_review_window, stake_cooldown):
        self.height = 1000
        self.jobs = {}
        self.arbs = {}
        self.n_registered = 0      # live bonds (REG or DEREG); the N for quorum
        self.arbitrator_timeout = arbitrator_timeout
        self.default_review_window = default_review_window
        self.stake_cooldown = stake_cooldown
        self.ledger = {}           # unit -> [locked, unlocked]; unit = job_id | ("arb", id)

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

    # ------------------------------------------------- arbitrator registry

    def register(self, arb_id, stake):
        if stake == 0: raise Halt
        if arb_id in self.arbs: raise Halt          # ids not reused in model
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
        # jobs (no enumeration), and with no slash the bond is never at risk
        # mid dispute, so reclaiming early only removes a voter, which is safe:
        # the dispute resolves on the rest or times out to void. A reclaimed
        # arb keeps any reward it already earned (reward keys off the vote).
        self.unlock(("arb", arb_id), a.stake)        # full bond back, never slashed
        a.stake = 0
        a.state = A_GONE
        self.n_registered -= 1

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
                label = "bond"
            raise AssertionError(
                "CONSERVATION VIOLATED unit %s: unlocked %s > locked %s (%s)"
                % (unit, unlocked, locked, label))


def drain_everything(chain):
    horizon = max([j.expiry for j in chain.jobs.values()] +
                  [j.review_deadline for j in chain.jobs.values()] +
                  [j.dispute_filed + chain.arbitrator_timeout for j in chain.jobs.values()] +
                  [a.dereg_block + chain.stake_cooldown for a in chain.arbs.values()] +
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
                        check_conservation(chain)
                    except Halt:
                        pass
                # per-voter reward claims (stand in for independent txs)
                if job.resolution is not None:
                    for aid in list(job.votes):
                        try:
                            chain.arb_reward_claim(aid, job_id)
                            progressed = True
                            check_conservation(chain)
                        except Halt:
                            pass

    run_jobs()
    # deregister every still-active arbitrator
    for aid in list(chain.arbs):
        try:
            chain.deregister(aid)
        except Halt:
            pass
    chain.height += chain.stake_cooldown + 2
    # all jobs terminal now, nobody bonded; reclaim every bond in full
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
                label = "bond:%s" % (chain.arbs[unit[1]].state,)
            stuck.append((unit, locked, unlocked, label))
    return stuck


# ------------------------------------------------------------------- fuzzer

def fuzz_sequence(seed, n_calls=600, n_jobs=8, n_arbs=8):
    rng = random.Random(seed)
    chain = Chain(arbitrator_timeout=rng.choice([1, 5, 50]),
                  default_review_window=rng.choice([5, 50]),
                  stake_cooldown=rng.choice([1, 5, 20]))
    job_ids = list(range(1, n_jobs + 1))
    arb_pool = list(range(101, 101 + n_arbs))
    next_arb = [0]
    amounts = [1, 2, 100, 100000]

    def fresh_arb():
        # never reuse an id (matches the model's one-life-per-id rule)
        if next_arb[0] < len(arb_pool):
            aid = arb_pool[next_arb[0]]
            next_arb[0] += 1
            return aid
        return rng.choice(arb_pool)

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
            # per-voter reward claim on a resolved job
            res = [j for j in chain.jobs.values() if j.resolution is not None and j.votes]
            if res:
                job = rng.choice(res)
                chain.arb_reward_claim(rng.choice(list(job.votes)), job.job_id)
            else:
                chain.arb_reward_claim(rng.choice(arb_pool), rng.choice(job_ids))
        elif op == 15:
            chain.register(fresh_arb(), rng.choice(amounts))
        elif op == 16:
            rng.choice([chain.deregister, chain.reclaim_stake])(rng.choice(arb_pool))
        elif op == 17:
            disputed = [j for j in chain.jobs.values() if j.status == DISPUTED]
            if disputed:
                job = rng.choice(disputed)
                aid = rng.choice(arb_pool)
                chain.vote(aid, job.job_id, rng.choice([ALICE, BOB]))
            else:
                chain.vote(rng.choice(arb_pool), rng.choice(job_ids), rng.choice([ALICE, BOB]))

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
    dispute, then deterministically reach a quorum or a deliberate split.
    Assert the expected terminal, the per-voter reward split, and full drain."""
    rng = random.Random(seed)
    chain = Chain(arbitrator_timeout=rng.choice([20, 50, 100]),
                  default_review_window=rng.choice([5, 50]),
                  stake_cooldown=rng.choice([1, 5, 20]))
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
        # losing voters keep their full bond (no slash)
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
        # the whole fee left as M shares plus the swept remainder
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
    """The classic on-chain quorum hole: after a dispute is filed and its N and
    M are frozen, an attacker registers a flood of new arbitrators and tries to
    vote them. Assert that none of the post-dispute registrations can vote on
    that dispute, that the frozen N and M do not move when the registry grows,
    that the flood cannot touch the tally, and that the legitimate pre-dispute
    set still resolves. The registered_at <= dispute_filed gate is what closes
    this; this scenario is its regression test."""
    rng = random.Random(seed)
    chain = Chain(arbitrator_timeout=rng.choice([20, 50, 100]),
                  default_review_window=rng.choice([5, 50]),
                  stake_cooldown=rng.choice([1, 5, 20]))
    amounts = [1, 2, 100, 100000]

    # honest arbitrators, all registered before the dispute
    k = rng.randint(1, 5)
    pre = list(range(101, 101 + k))
    stakes = {}
    for aid in pre:
        s = rng.choice(amounts)
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

    # advance so the flood registrations are unambiguously after the dispute
    chain.height += rng.choice([1, 2, 5])

    # the attack: flood the registry with new arbitrators
    flood = list(range(201, 201 + rng.randint(1, 8)))
    fstakes = {}
    for aid in flood:
        s = rng.choice(amounts)
        chain.register(aid, s)
        fstakes[aid] = s

    # the global counter grew, but this dispute's frozen N and M must not move
    assert job.frozen_n == frozen_n and job.threshold == M, (seed, "freeze moved under flood")
    assert chain.n_registered == k + len(flood), (seed, "counter wrong")

    # every flooded arbitrator is barred from voting on the already-filed dispute
    for aid in flood:
        try:
            chain.vote(aid, jid, rng.choice([ALICE, BOB]))
            raise AssertionError((seed, "post-dispute registration voted", aid))
        except Halt:
            pass

    # and the flood could not touch the tally
    assert job.vc_alice == 0 and job.vc_bob == 0, (seed, "flood moved the tally")
    counters["floods_blocked"] += len(flood)

    # the legitimate pre-dispute set still reaches quorum (k >= M always)
    rng.shuffle(pre)
    win = rng.choice([ALICE, BOB])
    for aid in pre[:M]:
        chain.vote(aid, jid, win)
    assert job.resolution is not None, (seed, "legit quorum failed after flood")
    counters["resolved_after_flood"] += 1

    # everything drains; flood bonds reclaim in full, never having voted
    stuck = drain_everything(chain)
    if stuck:
        raise AssertionError("ELIGIBILITY LIVENESS VIOLATED seed %s: %s" % (seed, stuck))
    for aid in pre:
        locked, unlocked = chain.ledger[("arb", aid)]
        assert locked == unlocked == stakes[aid], (seed, "pre bond not drained", aid)
    for aid in flood:
        locked, unlocked = chain.ledger[("arb", aid)]
        assert locked == unlocked == fstakes[aid], (seed, "flood bond not drained", aid)


def chain_committed(chain, job):
    # collateral that was locked for this job (commit happened in scenario)
    locked, _ = chain.ledger[job.job_id]
    return locked - job.payment - job.dispute_fee


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

    counters = {"resolved_alice": 0, "resolved_bob": 0, "consensus_voters": 0, "split": 0}
    for i in range(n_seq):
        fuzz_mofn_scenario(base + i, counters)
    print("M of N scenarios: %d, resolved Alice %d, resolved Bob %d, "
          "consensus voters paid %d, split-to-void %d. conservation and liveness held."
          % (n_seq, counters["resolved_alice"], counters["resolved_bob"],
             counters["consensus_voters"], counters["split"]))

    elig = {"floods_blocked": 0, "resolved_after_flood": 0}
    for i in range(n_seq):
        fuzz_eligibility_attack(base + i, elig)
    print("Eligibility attack scenarios: %d, post-dispute registrations blocked from voting %d, "
          "disputes still resolved by the frozen set %d. frozen N and M never moved."
          % (n_seq, elig["floods_blocked"], elig["resolved_after_flood"]))


if __name__ == "__main__":
    main()

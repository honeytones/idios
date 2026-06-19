#include "Shaders/common.h"
#include "Shaders/Math.h"
#include "idios_contract.h"
#include "Shaders/upgradable3/contract_impl.h" // provides Method_2 (Upgradable3 control) + Settings impl

// KeyJob and KeyParams live in idios_contract.h inside its packed region (v5
// KeyJob padding fix): the contract and the app shader serialize the same
// deterministic key bytes. The M of N key structs live there too.
using Idios::KeyJob;
using Idios::KeyParams;
using Idios::KeyDispute;
using Idios::KeyArb;
using Idios::KeyVote;
using Idios::KeyRegCount;

// ---- storage helpers ------------------------------------------------------

static bool LoadJob(uint64_t job_id, Idios::Job& job) {
    KeyJob key;
    key.job_id = job_id;
    uint32_t n = Env::LoadVar(&key, sizeof(key), &job, sizeof(job), KeyTag::Internal);
    return n == sizeof(job);
}

static void SaveJob(const Idios::Job& job) {
    KeyJob key;
    key.job_id = job.job_id;
    Env::SaveVar(&key, sizeof(key), &job, sizeof(job), KeyTag::Internal);
    Env::EmitLog(&key, sizeof(key), &job, sizeof(job), KeyTag::Internal);
}

static bool LoadParams(Idios::Params& params) {
    KeyParams key;
    uint32_t n = Env::LoadVar(&key, sizeof(key), &params, sizeof(params), KeyTag::Internal);
    return n == sizeof(params);
}

static bool LoadDispute(uint64_t job_id, Idios::DisputeState& ds) {
    KeyDispute key;
    key.job_id = job_id;
    uint32_t n = Env::LoadVar(&key, sizeof(key), &ds, sizeof(ds), KeyTag::Internal);
    return n == sizeof(ds);
}

static void SaveDispute(uint64_t job_id, const Idios::DisputeState& ds) {
    KeyDispute key;
    key.job_id = job_id;
    Env::SaveVar(&key, sizeof(key), &ds, sizeof(ds), KeyTag::Internal);
    Env::EmitLog(&key, sizeof(key), &ds, sizeof(ds), KeyTag::Internal);
}

static bool LoadArb(const PubKey& pk, Idios::ArbRec& a) {
    KeyArb key;
    Env::Memcpy(&key.arb_pk, &pk, sizeof(PubKey));
    uint32_t n = Env::LoadVar(&key, sizeof(key), &a, sizeof(a), KeyTag::Internal);
    return n == sizeof(a);
}

static bool ArbExists(const PubKey& pk) {
    KeyArb key;
    Env::Memcpy(&key.arb_pk, &pk, sizeof(PubKey));
    Idios::ArbRec a;
    uint32_t n = Env::LoadVar(&key, sizeof(key), &a, sizeof(a), KeyTag::Internal);
    return n > 0;
}

static void SaveArb(const PubKey& pk, const Idios::ArbRec& a) {
    KeyArb key;
    Env::Memcpy(&key.arb_pk, &pk, sizeof(PubKey));
    Env::SaveVar(&key, sizeof(key), &a, sizeof(a), KeyTag::Internal);
    Env::EmitLog(&key, sizeof(key), &a, sizeof(a), KeyTag::Internal);
}

static bool LoadVote(uint64_t job_id, const PubKey& pk, Idios::VoteRec& v) {
    KeyVote key;
    key.job_id = job_id;
    Env::Memcpy(&key.arb_pk, &pk, sizeof(PubKey));
    uint32_t n = Env::LoadVar(&key, sizeof(key), &v, sizeof(v), KeyTag::Internal);
    return n == sizeof(v);
}

static bool VoteExists(uint64_t job_id, const PubKey& pk) {
    KeyVote key;
    key.job_id = job_id;
    Env::Memcpy(&key.arb_pk, &pk, sizeof(PubKey));
    Idios::VoteRec v;
    uint32_t n = Env::LoadVar(&key, sizeof(key), &v, sizeof(v), KeyTag::Internal);
    return n > 0;
}

static void SaveVote(uint64_t job_id, const PubKey& pk, const Idios::VoteRec& v) {
    KeyVote key;
    key.job_id = job_id;
    Env::Memcpy(&key.arb_pk, &pk, sizeof(PubKey));
    Env::SaveVar(&key, sizeof(key), &v, sizeof(v), KeyTag::Internal);
    Env::EmitLog(&key, sizeof(key), &v, sizeof(v), KeyTag::Internal);
}

static uint64_t LoadRegCount() {
    KeyRegCount key;
    Idios::RegCount rc;
    rc.n = 0;
    uint32_t n = Env::LoadVar(&key, sizeof(key), &rc, sizeof(rc), KeyTag::Internal);
    return (n == sizeof(rc)) ? rc.n : 0;
}

static void SaveRegCount(uint64_t value) {
    KeyRegCount key;
    Idios::RegCount rc;
    rc.n = value;
    Env::SaveVar(&key, sizeof(key), &rc, sizeof(rc), KeyTag::Internal);
}

static bool HashesMatch(const uint8_t* a, const uint8_t* b) {
    return Env::Memcmp(a, b, 32) == 0;
}

static bool JobIdInUse(uint64_t job_id) {
    Idios::Job existing;
    KeyJob key;
    key.job_id = job_id;
    uint32_t n = Env::LoadVar(&key, sizeof(key), &existing, sizeof(existing), KeyTag::Internal);
    return n > 0;
}

// ---- ctor / dtor ----------------------------------------------------------

BEAM_EXPORT void Ctor(const Idios::Create& r) {
    // Upgradable3: validate and persist the admin settings first (amm pattern).
    r.m_Upgradable.TestNumApprovers();
    r.m_Upgradable.Save();

    const Idios::Params& params = r.m_Params;
    Env::Halt_if(Env::Memis0(&params.arbitrator_pk, sizeof(params.arbitrator_pk)));
    Env::Halt_if(Env::Memis0(&params.treasury_pk, sizeof(params.treasury_pk)));
    Env::Halt_if(params.default_review_window == 0);
    Env::Halt_if(params.arbitrator_timeout_blocks == 0);
    KeyParams key;
    Env::SaveVar(&key, sizeof(key), &params, sizeof(params), KeyTag::Internal);
}

BEAM_EXPORT void Dtor(void*) {}

// ---- escrow (v5/v6, unchanged) --------------------------------------------

BEAM_EXPORT void Method_4(const Idios::CreateModeA& args) {
    Env::Halt_if(args.payment == 0);
    Env::Halt_if(Env::Memis0(&args.node_pk, sizeof(args.node_pk)));
    Env::Halt_if(Env::Memis0(&args.requester_pk, sizeof(args.requester_pk)));
    Env::Halt_if(Env::Memis0(args.result_hash, 32));
    Env::Halt_if(args.expiry_block <= Env::get_Height());
    Env::Halt_if(JobIdInUse(args.job_id));

    Idios::Job job;
    Env::Memset(&job, 0, sizeof(job));
    job.job_id              = args.job_id;
    job.subnet_id           = args.subnet_id;
    job.epoch               = args.epoch;
    job.expiry_block        = args.expiry_block;
    job.payment             = args.payment;
    job.collateral          = 0;
    job.required_collateral = args.required_collateral; // v5
    job.asset_id            = args.asset_id;
    job.mode                = Idios::JobMode::ModeA;
    job.status              = Idios::JobStatus::Open;
    Env::Memcpy(&job.node_pk,      &args.node_pk,      sizeof(PubKey));
    Env::Memcpy(&job.requester_pk, &args.requester_pk, sizeof(PubKey));
    Env::Memcpy(job.result_hash,   args.result_hash,   32);
    Env::Memcpy(job.spec_hash,     args.spec_hash,     32); // v5: optional, may be zero

    Env::AddSig(args.requester_pk);
    Env::FundsLock(args.asset_id, args.payment);
    SaveJob(job);
}

BEAM_EXPORT void Method_3(const Idios::Commit& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));
    Env::Halt_if(job.status != Idios::JobStatus::Open);
    Env::Halt_if(Env::get_Height() >= job.expiry_block);
    Env::Halt_if(args.collateral == 0);
    Env::Halt_if(args.collateral < job.required_collateral); // v5 floor; 0 = none
    Env::Halt_if(args.asset_id != job.asset_id);

    Env::AddSig(job.node_pk);
    Env::FundsLock(args.asset_id, args.collateral);
    job.collateral = args.collateral;
    job.status     = Idios::JobStatus::Active;
    SaveJob(job);
}

// Method_2 is the Upgradable3 control dispatch (upgradable3/contract_impl.h).
BEAM_EXPORT void Method_5(void*) { Env::Halt(); }

BEAM_EXPORT void Method_6(const Idios::Refund& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));
    Env::Halt_if(job.status != Idios::JobStatus::Open &&
                 job.status != Idios::JobStatus::Active);
    Env::Halt_if(Env::get_Height() <= job.expiry_block);

    Env::AddSig(job.requester_pk);
    // payment back to requester; any Active-path collateral is forfeit and
    // stays locked for TreasurySweep (Method_19). Open jobs have collateral 0.
    Env::FundsUnlock(job.asset_id, job.payment);
    job.status = Idios::JobStatus::Refunded;
    SaveJob(job);
}

BEAM_EXPORT void Method_7(void*) { Env::Halt(); }

BEAM_EXPORT void Method_8(const Idios::CreateModeB& args) {
    Env::Halt_if(args.payment == 0);
    Env::Halt_if(args.dispute_fee == 0);
    Env::Halt_if(Env::Memis0(&args.node_pk, sizeof(args.node_pk)));
    Env::Halt_if(Env::Memis0(&args.requester_pk, sizeof(args.requester_pk)));
    Env::Halt_if(args.expiry_block <= Env::get_Height());
    Env::Halt_if(JobIdInUse(args.job_id));

    Height review_window = args.review_window_blocks;
    if (review_window == 0) {
        Idios::Params params;
        Env::Halt_if(!LoadParams(params));
        review_window = params.default_review_window;
    }

    Idios::Job job;
    Env::Memset(&job, 0, sizeof(job));
    job.job_id               = args.job_id;
    job.subnet_id            = args.subnet_id;
    job.epoch                = args.epoch;
    job.expiry_block         = args.expiry_block;
    job.review_window_blocks = review_window;
    job.payment              = args.payment;
    job.collateral           = 0;
    job.required_collateral  = args.required_collateral; // v5
    job.dispute_fee          = args.dispute_fee;
    job.asset_id             = args.asset_id;
    job.mode                 = Idios::JobMode::ModeB;
    job.status               = Idios::JobStatus::Open;
    Env::Memcpy(&job.node_pk,      &args.node_pk,      sizeof(PubKey));
    Env::Memcpy(&job.requester_pk, &args.requester_pk, sizeof(PubKey));
    Env::Memcpy(job.spec_hash,     args.spec_hash,     32); // v5: optional, may be zero

    Env::AddSig(args.requester_pk);
    Env::FundsLock(args.asset_id, args.payment);
    SaveJob(job);
}

BEAM_EXPORT void Method_9(const Idios::SubmitDelivery& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));
    Env::Halt_if(job.status != Idios::JobStatus::Active);
    Env::Halt_if(Env::get_Height() >= job.expiry_block);

    Env::AddSig(job.node_pk);
    Env::Memcpy(job.delivery_hash, args.delivery_hash, 32);

    if (job.mode == Idios::JobMode::ModeA) {
        Env::Halt_if(!HashesMatch(args.delivery_hash, job.result_hash));
        Env::FundsUnlock(job.asset_id, job.payment + job.collateral);
        job.status = Idios::JobStatus::Closed; // Mode A auto-settles, terminal
    } else {
        job.review_deadline_block = Env::get_Height() + job.review_window_blocks;
        job.status = Idios::JobStatus::AwaitingApproval;
    }
    SaveJob(job);
}

BEAM_EXPORT void Method_10(const Idios::Approve& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));
    Env::Halt_if(job.mode != Idios::JobMode::ModeB);
    Env::Halt_if(job.status != Idios::JobStatus::AwaitingApproval);
    Env::Halt_if(Env::get_Height() > job.review_deadline_block);

    Env::AddSig(job.requester_pk);
    job.status = Idios::JobStatus::Settled;
    SaveJob(job);
}

// ---- M of N dispute path --------------------------------------------------

BEAM_EXPORT void Method_11(const Idios::Dispute& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));
    Env::Halt_if(job.mode != Idios::JobMode::ModeB);
    Env::Halt_if(job.status != Idios::JobStatus::AwaitingApproval);
    Env::Halt_if(Env::get_Height() > job.review_deadline_block);

    Env::AddSig(job.requester_pk);
    Env::FundsLock(job.asset_id, job.dispute_fee);
    job.dispute_filed_block = Env::get_Height();
    job.status = Idios::JobStatus::Disputed;
    SaveJob(job);

    // Freeze the live registry size N and the majority threshold M onto a per
    // dispute record. No set of arbitrator keys is stored (a method cannot
    // enumerate them); eligibility is checked per vote via registered_at.
    uint64_t n = LoadRegCount();
    Idios::DisputeState ds;
    Env::Memset(&ds, 0, sizeof(ds));
    ds.frozen_n  = n;
    ds.threshold = (n > 0) ? (uint32_t)(n / 2 + 1) : 1;
    ds.resolution = 0;
    SaveDispute(args.job_id, ds);
}

// Method_12 / Method_13: RETIRED. Single arbitrator resolve is replaced by
// quorum voting (Method_24). Halt so the old arbitrator key cannot resolve a
// v1 dispute unilaterally and bypass the quorum.
BEAM_EXPORT void Method_12(void*) { Env::Halt(); }
BEAM_EXPORT void Method_13(void*) { Env::Halt(); }

BEAM_EXPORT void Method_14(const Idios::ClaimAfterTimeout& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));
    Env::Halt_if(job.mode != Idios::JobMode::ModeB);
    Env::Halt_if(job.status != Idios::JobStatus::AwaitingApproval);
    Env::Halt_if(Env::get_Height() <= job.review_deadline_block);

    Env::AddSig(job.node_pk);
    job.status = Idios::JobStatus::Settled;
    SaveJob(job);
}

BEAM_EXPORT void Method_15(const Idios::Claim& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));

    if (job.status == Idios::JobStatus::Settled) {
        // approve or review timeout: winner takes P + C, terminal.
        Env::AddSig(job.node_pk);
        Env::FundsUnlock(job.asset_id, job.payment + job.collateral);
        job.status = Idios::JobStatus::Closed;
        SaveJob(job);
        return;
    }

    // Resolved dispute (M of N): the winner takes P + C only. The dispute_fee
    // is the arbitration reward, claimed per voter (Method_25), remainder
    // swept to treasury (Method_19). Guard double payment with ds.winner_paid;
    // the job stays Resolved so the per voter reward claims still apply.
    Idios::DisputeState ds;
    Env::Halt_if(!LoadDispute(args.job_id, ds));
    Env::Halt_if(ds.winner_paid);

    if (job.status == Idios::JobStatus::ResolvedToAlice) {
        Env::AddSig(job.requester_pk);
    } else if (job.status == Idios::JobStatus::ResolvedToBob) {
        Env::AddSig(job.node_pk);
    } else {
        Env::Halt();
    }
    Env::FundsUnlock(job.asset_id, job.payment + job.collateral);
    ds.winner_paid = 1;
    SaveDispute(args.job_id, ds);
}

// ---- arbitrator-timeout void path (unchanged) -----------------------------

BEAM_EXPORT void Method_16(const Idios::VoidStaleDispute& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));
    Env::Halt_if(job.status != Idios::JobStatus::Disputed);

    Idios::Params params;
    Env::Halt_if(!LoadParams(params));
    Env::Halt_if(Env::get_Height() <=
                 job.dispute_filed_block + params.arbitrator_timeout_blocks);

    job.status = Idios::JobStatus::Voided;
    SaveJob(job);
}

BEAM_EXPORT void Method_17(const Idios::VoidClaimRequester& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));
    Env::Halt_if(job.status != Idios::JobStatus::Voided);
    Env::Halt_if(job.payment == 0);

    Env::AddSig(job.requester_pk);
    Env::FundsUnlock(job.asset_id, job.payment);
    job.payment = 0;
    SaveJob(job);
}

BEAM_EXPORT void Method_18(const Idios::VoidClaimNode& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));
    Env::Halt_if(job.status != Idios::JobStatus::Voided);
    Env::Halt_if(job.collateral == 0);

    Env::AddSig(job.node_pk);
    Env::FundsUnlock(job.asset_id, job.collateral);
    job.collateral = 0;
    SaveJob(job);
}

// Method_19: treasury sweep. Collects funds forfeit to the protocol:
//   - Refunded jobs: the node's collateral (non-delivery penalty),
//   - Voided jobs:   the unawardable dispute_fee,
//   - Resolved jobs: the M of N reward remainder F % M.
// Each portion is taken once (zeroed or flagged) to prevent a second pull.
BEAM_EXPORT void Method_19(const Idios::TreasurySweep& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));

    Idios::Params params;
    Env::Halt_if(!LoadParams(params));
    Env::AddSig(params.treasury_pk);

    if (job.status == Idios::JobStatus::Refunded && job.collateral > 0) {
        Env::FundsUnlock(job.asset_id, job.collateral);
        job.collateral = 0;
        SaveJob(job);
    } else if (job.status == Idios::JobStatus::Voided && job.dispute_fee > 0) {
        Env::FundsUnlock(job.asset_id, job.dispute_fee);
        job.dispute_fee = 0;
        SaveJob(job);
    } else {
        Idios::DisputeState ds;
        Env::Halt_if(!LoadDispute(args.job_id, ds));
        Env::Halt_if(ds.resolution == 0 || ds.fee_remainder == 0 || ds.remainder_swept);
        Env::FundsUnlock(job.asset_id, ds.fee_remainder);
        ds.remainder_swept = 1;
        SaveDispute(args.job_id, ds);
    }
}

// Method_20 (v5): mutual cancel. Both sign, everyone made whole in this tx.
// Active or AwaitingApproval only. Not Open (refund covers it), not Disputed
// (would strand the locked dispute_fee and dodge a pending ruling).
BEAM_EXPORT void Method_20(const Idios::MutualCancel& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));
    Env::Halt_if(job.status != Idios::JobStatus::Active &&
                 job.status != Idios::JobStatus::AwaitingApproval);

    Env::AddSig(job.requester_pk);
    Env::AddSig(job.node_pk);
    Env::FundsUnlock(job.asset_id, job.payment + job.collateral);
    job.status = Idios::JobStatus::Cancelled;
    SaveJob(job);
}

// ---- M of N (v1) methods --------------------------------------------------

// Method_21: register as an arbitrator. Lock a standing bond (pure sybil
// resistance in v1, never slashed) and bump the live registry counter N.
BEAM_EXPORT void Method_21(const Idios::Register& args) {
    Env::Halt_if(args.stake == 0);
    Env::Halt_if(Env::Memis0(&args.arb_pk, sizeof(args.arb_pk)));
    Env::Halt_if(ArbExists(args.arb_pk)); // ids are not reused in v1

    Env::AddSig(args.arb_pk);
    Env::FundsLock(args.asset_id, args.stake);

    Idios::ArbRec a;
    Env::Memset(&a, 0, sizeof(a));
    a.stake         = args.stake;
    a.asset_id      = args.asset_id;
    a.registered_at = Env::get_Height();
    a.dereg_block   = 0;
    a.state         = 0; // registered
    SaveArb(args.arb_pk, a);

    SaveRegCount(LoadRegCount() + 1);
}

// Method_22: begin deregistering. Still counts toward N and may still vote on
// disputes filed before now, until the bond is reclaimed.
BEAM_EXPORT void Method_22(const Idios::Deregister& args) {
    Idios::ArbRec a;
    Env::Halt_if(!LoadArb(args.arb_pk, a));
    Env::Halt_if(a.state != 0); // must be registered

    Env::AddSig(args.arb_pk);
    a.state       = 1; // deregistering
    a.dereg_block = Env::get_Height();
    SaveArb(args.arb_pk, a);
}

// Method_23: reclaim the bond in full after the cooldown (reusing
// arbitrator_timeout_blocks). No "still bonded" gate: a method cannot scan
// jobs, and with no slash the bond is never at risk mid dispute. Decrement N.
BEAM_EXPORT void Method_23(const Idios::ReclaimStake& args) {
    Idios::ArbRec a;
    Env::Halt_if(!LoadArb(args.arb_pk, a));
    Env::Halt_if(a.state != 1); // must be deregistering

    Idios::Params params;
    Env::Halt_if(!LoadParams(params));
    Env::Halt_if(Env::get_Height() <= a.dereg_block + params.arbitrator_timeout_blocks);

    Env::AddSig(args.arb_pk);
    Env::FundsUnlock(a.asset_id, a.stake);
    a.stake = 0;
    a.state = 2; // gone
    SaveArb(args.arb_pk, a);

    uint64_t n = LoadRegCount();
    if (n > 0) n -= 1;
    SaveRegCount(n);
}

// Method_24: cast one immutable vote on a disputed job. Only arbitrators that
// were registered before the dispute, and still hold a bond, may vote. The
// Mth matching vote resolves the job.
BEAM_EXPORT void Method_24(const Idios::Vote& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));
    Env::Halt_if(job.mode != Idios::JobMode::ModeB);
    Env::Halt_if(job.status != Idios::JobStatus::Disputed);
    Env::Halt_if(args.side > 1);

    Idios::ArbRec a;
    Env::Halt_if(!LoadArb(args.arb_pk, a));
    Env::Halt_if(a.stake == 0);
    Env::Halt_if(a.registered_at > job.dispute_filed_block); // pre-dispute only

    Env::Halt_if(VoteExists(args.job_id, args.arb_pk));       // one immutable vote

    Idios::DisputeState ds;
    Env::Halt_if(!LoadDispute(args.job_id, ds));
    Env::Halt_if(ds.resolution != 0);

    Env::AddSig(args.arb_pk);

    Idios::VoteRec v;
    Env::Memset(&v, 0, sizeof(v));
    v.side    = args.side;
    v.claimed = 0;
    SaveVote(args.job_id, args.arb_pk, v);

    uint32_t tally;
    if (args.side == 0) { ds.vc_alice += 1; tally = ds.vc_alice; }
    else                { ds.vc_bob   += 1; tally = ds.vc_bob; }

    if (tally >= ds.threshold) {
        ds.resolution     = (args.side == 0) ? 1 : 2;
        ds.fee_share      = job.dispute_fee / ds.threshold;
        ds.fee_remainder  = job.dispute_fee - ds.fee_share * ds.threshold;
        job.status = (args.side == 0) ? Idios::JobStatus::ResolvedToAlice
                                      : Idios::JobStatus::ResolvedToBob;
        SaveJob(job);
    }
    SaveDispute(args.job_id, ds);
}

// Method_25: a consensus voter claims its F / M reward share. Keys off the
// recorded vote, not the bond, so a reclaimed arbitrator can still collect.
BEAM_EXPORT void Method_25(const Idios::ClaimArbReward& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));

    Idios::DisputeState ds;
    Env::Halt_if(!LoadDispute(args.job_id, ds));
    Env::Halt_if(ds.resolution == 0);

    Idios::VoteRec v;
    Env::Halt_if(!LoadVote(args.job_id, args.arb_pk, v));

    uint8_t winning_side = (ds.resolution == 1) ? 0 : 1;
    Env::Halt_if(v.side != winning_side); // must be a consensus voter
    Env::Halt_if(v.claimed);

    Env::AddSig(args.arb_pk);
    if (ds.fee_share > 0)
        Env::FundsUnlock(job.asset_id, ds.fee_share);
    v.claimed = 1;
    SaveVote(args.job_id, args.arb_pk, v);
}

// ---- Upgradable3 version callbacks ----------------------------------------
// g_CurrentVersion bumped 0 -> 1 for the M of N in place upgrade. OnUpgraded
// enforces a single step: an upgrade from v0 must pass nPrevVersion == 0.
namespace Upgradable3 {

    static const uint32_t g_CurrentVersion = 1;

    uint32_t get_CurrentVersion() {
        return g_CurrentVersion;
    }

    void OnUpgraded(uint32_t nPrevVersion) {
        if constexpr (g_CurrentVersion)
            Env::Halt_if(nPrevVersion != g_CurrentVersion - 1);
        else
            Env::Halt();
    }
}

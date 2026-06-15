#include "Shaders/common.h"
#include "Shaders/Math.h"
#include "idios_contract.h"
#include "Shaders/upgradable3/contract_impl.h" // provides Method_2 (Upgradable3 control) + Settings impl

// KeyJob and KeyParams now live in idios_contract.h inside its packed region
// (v5 KeyJob padding fix): both the contract and the app shader serialize the
// same deterministic 9-byte key.
using Idios::KeyJob;
using Idios::KeyParams;

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
    // v5: the requester sets a collateral floor at create; a worker cannot
    // lock the requester in with dust collateral. 0 means no floor.
    Env::Halt_if(args.collateral < job.required_collateral);
    Env::Halt_if(args.asset_id != job.asset_id);

    Env::AddSig(job.node_pk);
    Env::FundsLock(args.asset_id, args.collateral);
    job.collateral = args.collateral;
    job.status     = Idios::JobStatus::Active;
    SaveJob(job);
}

// Method_2 is the Upgradable3 control dispatch, provided by the included
// upgradable3/contract_impl.h. Method_4 is now CreateModeA (above).
BEAM_EXPORT void Method_5(void*) { Env::Halt(); }

BEAM_EXPORT void Method_6(const Idios::Refund& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));
    // Refund is valid only before settlement/resolution: Open (no node
    // committed yet) or Active (node committed but failed to deliver by
    // expiry). Explicit allow-list on purpose -- the previous deny-list
    // silently permitted Refund from ResolvedToAlice/ResolvedToBob/Closed,
    // letting a requester race the rightful claimant or double-unlock.
    Env::Halt_if(job.status != Idios::JobStatus::Open &&
                 job.status != Idios::JobStatus::Active);
    Env::Halt_if(Env::get_Height() <= job.expiry_block);

    Env::AddSig(job.requester_pk);
    // Requester is made whole (payment returned). On the Active path the
    // node's collateral is forfeit and intentionally NOT returned to the
    // requester -- it stays locked until the treasury claims it via
    // TreasurySweep (Method_19), so the requester cannot profit from
    // inducing non-delivery. Open jobs have collateral == 0.
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

    // v5: review_window_blocks == 0 means use the contract default, which
    // finally wires the previously dead params.default_review_window.
    Height review_window = args.review_window_blocks;
    if (review_window == 0) {
        Idios::Params params;
        Env::Halt_if(!LoadParams(params));
        review_window = params.default_review_window;
    }

    Idios::Job job;
    Env::Memset(&job, 0, sizeof(job));
    job.job_id              = args.job_id;
    job.subnet_id           = args.subnet_id;
    job.epoch               = args.epoch;
    job.expiry_block        = args.expiry_block;
    job.review_window_blocks = review_window;
    job.payment             = args.payment;
    job.collateral          = 0;
    job.required_collateral = args.required_collateral; // v5
    job.dispute_fee         = args.dispute_fee;
    job.asset_id            = args.asset_id;
    job.mode                = Idios::JobMode::ModeB;
    job.status              = Idios::JobStatus::Open;
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
        // Mode A auto-settles here; mark terminal so Claim's Settled branch
        // cannot unlock a second time. (Was: Settled.)
        job.status = Idios::JobStatus::Closed;
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
}

BEAM_EXPORT void Method_12(const Idios::ResolveToAlice& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));
    Env::Halt_if(job.mode != Idios::JobMode::ModeB);
    Env::Halt_if(job.status != Idios::JobStatus::Disputed);

    Idios::Params params;
    Env::Halt_if(!LoadParams(params));

    Env::AddSig(params.arbitrator_pk);
    job.status = Idios::JobStatus::ResolvedToAlice;
    SaveJob(job);
}

BEAM_EXPORT void Method_13(const Idios::ResolveToBob& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));
    Env::Halt_if(job.mode != Idios::JobMode::ModeB);
    Env::Halt_if(job.status != Idios::JobStatus::Disputed);

    Idios::Params params;
    Env::Halt_if(!LoadParams(params));

    Env::AddSig(params.arbitrator_pk);
    job.status = Idios::JobStatus::ResolvedToBob;
    SaveJob(job);
}

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
        Env::AddSig(job.node_pk);
        Env::FundsUnlock(job.asset_id, job.payment + job.collateral);
    } else if (job.status == Idios::JobStatus::ResolvedToBob) {
        Env::AddSig(job.node_pk);
        Env::FundsUnlock(job.asset_id, job.payment + job.collateral + job.dispute_fee);
    } else if (job.status == Idios::JobStatus::ResolvedToAlice) {
        Env::AddSig(job.requester_pk);
        Env::FundsUnlock(job.asset_id, job.payment + job.collateral + job.dispute_fee);
    } else {
        Env::Halt();
    }

    job.status = Idios::JobStatus::Closed;
    SaveJob(job);
}

// ---------------------------------------------------------------------------
// Arbitrator-timeout path: if a dispute is never resolved, neither party
// should be able to win by stalling, and an innocent party should not lose
// their own stake. So a stale dispute is Voided: each party reclaims their
// own principal, and the unawardable dispute_fee goes to the treasury.
// ---------------------------------------------------------------------------

// Method_16: flip a dispute the arbitrator never resolved into Voided.
// Permissionless on purpose -- no AddSig. It moves no funds and is gated by
// an objective on-chain timeout, so anyone may trigger it and neither party
// can block the other. (If the toolchain requires a signer, gate on
// job.requester_pk; see app handler note.)
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

// Method_17: requester reclaims their payment from a voided dispute.
// payment is zeroed on success so it cannot be pulled twice.
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

// Method_18: node reclaims their collateral from a voided dispute.
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

// Method_19: treasury sweep. Collects the only funds forfeit to the protocol:
//   - Refunded jobs: the node's collateral (non-delivery penalty), and
//   - Voided jobs:   the unawardable dispute_fee.
// Each portion is zeroed on sweep to prevent a second pull.
BEAM_EXPORT void Method_19(const Idios::TreasurySweep& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));

    Idios::Params params;
    Env::Halt_if(!LoadParams(params));
    Env::AddSig(params.treasury_pk);

    if (job.status == Idios::JobStatus::Refunded && job.collateral > 0) {
        Env::FundsUnlock(job.asset_id, job.collateral);
        job.collateral = 0;
    } else if (job.status == Idios::JobStatus::Voided && job.dispute_fee > 0) {
        Env::FundsUnlock(job.asset_id, job.dispute_fee);
        job.dispute_fee = 0;
    } else {
        Env::Halt();
    }
    SaveJob(job);
}

// ---------------------------------------------------------------------------
// Method_20 (v5): mutual cancel. Both parties sign and everyone is made whole:
// payment returns to the requester, collateral returns to the node, in this
// transaction (no separate claim). Allowed from Active (worker committed,
// cannot or will not deliver, both agree to walk away) and AwaitingApproval
// (delivery happened but both agree to unwind). NOT allowed from Open (the
// requester's refund path covers it, no counterparty has funds at risk) and
// NOT from Disputed: cancelling around a filed dispute would strand the locked
// dispute_fee with no recovery path (proven by the conservation fuzzer) and
// would let a party dodge a pending ruling.
// ---------------------------------------------------------------------------
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

// ---------------------------------------------------------------------------
// Upgradable3 version callbacks. g_CurrentVersion starts at 0 for this first
// upgradable deploy and is bumped by hand on each in place upgrade. OnUpgraded
// enforces that an upgrade steps the version by exactly one; at version 0 there
// is no prior version to upgrade from, so it halts.
// ---------------------------------------------------------------------------
namespace Upgradable3 {

    static const uint32_t g_CurrentVersion = 0;

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

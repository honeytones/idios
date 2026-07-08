#include "Shaders/common.h"
#include "Shaders/app_common_impl.h"
#include "idios_contract.h"
#include "Shaders/upgradable3/app_common_impl.h" // Upgradable3 manager driver (schedule/explicit upgrade)

using Action_func_t = void (*)(const ContractID&);

struct ActionEntry {
    const char* name;
    Action_func_t handler;
};

struct RoleEntry {
    const char* name;
    const ActionEntry* actions;
    uint32_t action_count;
};

constexpr uint32_t ACTION_BUF_SIZE = 32;
constexpr uint32_t ROLE_BUF_SIZE = 16;

static int str_eq(const char* a, const char* b)
{
    while (*a && *b) {
        if (*a != *b) return 0;
        ++a; ++b;
    }
    return *a == *b;
}

void On_error(const char* msg)
{
    Env::DocGroup root("");
    Env::DocAddText("error", msg);
}

// ----------------------------------------------------------------
//  KeyID helper structs (used for signature derivation)
// ----------------------------------------------------------------

struct UserKeyID {
    ContractID m_Cid;
    uint8_t    m_Ctx = 0;
};

struct ArbitratorKeyID {
    uint8_t m_Tag = 'A';
    uint8_t m_Ctx = 1;
};

struct TreasuryKeyID {
    uint8_t m_Tag = 'T';
    uint8_t m_Ctx = 1;
};

struct AdminKeyID {
    uint8_t m_Tag = 'M'; // manager/admin key for Upgradable3, wallet fixed like 'A' and 'T'
    uint8_t m_Ctx = 1;
};

// M of N arbitrator key. Wallet fixed like 'A'/'T'/'M', but parameterized by
// an index so a single wallet can hold several distinct arbitrator identities
// (needed to drive a 2 of 3 from one wallet in testing). Separate wallets can
// all use index 0 and still derive distinct keys, since DerivePk is per wallet.
struct MofnArbKeyID {
    uint8_t  m_Tag = 'N';
    uint8_t  m_Ctx = 1;
    uint32_t m_Idx = 0;
};

// ----------------------------------------------------------------
//  Manager actions (deploy, view)
// ----------------------------------------------------------------

void On_manager_deploy(const ContractID& unused)
{
    Idios::Create create;
    Env::Memset(&create, 0, sizeof(create));

    Idios::Params& params = create.m_Params;

    ArbitratorKeyID kid;
    Env::DerivePk(params.arbitrator_pk, &kid, sizeof(kid));

    TreasuryKeyID tkid;
    Env::DerivePk(params.treasury_pk, &tkid, sizeof(tkid));

    uint64_t review_window = 10080;
    uint64_t arbitrator_timeout = 20160;
    Env::DocGetNum64("default_review_window", &review_window);
    Env::DocGetNum64("arbitrator_timeout_blocks", &arbitrator_timeout);
    params.default_review_window = review_window;
    params.arbitrator_timeout_blocks = arbitrator_timeout;

    // Upgradable3 admin settings. Admin key is wallet fixed (derived from the
    // deploying CLI wallet), single approver, one day upgrade timelock by
    // default. Pass a small upgrade_delay for the throwaway test cid.
    AdminKeyID akid;
    Env::DerivePk(create.m_Upgradable.m_pAdmin[0], &akid, sizeof(akid));
    create.m_Upgradable.m_MinApprovers = 1;
    uint64_t upgrade_delay = 1440;
    Env::DocGetNum64("upgrade_delay", &upgrade_delay);
    create.m_Upgradable.m_hMinUpgradeDelay = upgrade_delay;

    Env::GenerateKernel(nullptr, 0, &create, sizeof(create),
        nullptr, 0, nullptr, 0, "Deploy Idios contract (Upgradable3)", 200000);
}

void On_manager_view(const ContractID& cid)
{
    struct KeyParams { uint8_t prefix = Idios::Tags::s_Params; };
    Idios::Params params;
    Env::Key_T<KeyParams> key;
    key.m_Prefix.m_Cid = cid;
    if (!Env::VarReader::Read_T(key, params))
        return On_error("params not found");

    Env::DocGroup gr("params");
    Env::DocAddBlob_T("arbitrator_pk", params.arbitrator_pk);
    Env::DocAddBlob_T("treasury_pk", params.treasury_pk);
    Env::DocAddNum64("default_review_window", params.default_review_window);
    Env::DocAddNum64("arbitrator_timeout_blocks", params.arbitrator_timeout_blocks);
}

// Live registry size N (the value frozen onto a dispute as its quorum base).
void On_manager_view_regcount(const ContractID& cid)
{
    Idios::RegCount rc;
    rc.n = 0;
    Env::Key_T<Idios::KeyRegCount> k;
    k.m_Prefix.m_Cid = cid;
    Env::VarReader::Read_T(k, rc); // absent => 0
    Env::DocGroup gr("regcount");
    Env::DocAddNum64("n_registered", rc.n);
}

// ----------------------------------------------------------------
//  Manager actions: Upgradable3 upgrade drivers
// ----------------------------------------------------------------
void On_manager_schedule_upgrade(const ContractID& cid)
{
    using SU = Upgradable3::Method::Control::ScheduleUpgrade;

    Upgradable3::Manager::SettingsPlus stg;
    if (!stg.Read(cid)) return On_error("upgradable settings not found");

    uint32_t nShaderSize = Env::DocGetBlob("contract.shader", nullptr, 0);
    if (!nShaderSize) return On_error("contract.shader not provided");

    uint32_t nArg = sizeof(SU) + nShaderSize;
    SU* pArg = (SU*) Env::Heap_Alloc(nArg);
    pArg->m_Type = SU::s_Type;
    pArg->m_ApproveMask = 1; // solo admin, bit 0
    pArg->m_SizeShader = nShaderSize;
    pArg->m_Next.m_hTarget = Env::get_Height() + stg.m_hMinUpgradeDelay + 10;
    Env::DocGetBlob("contract.shader", pArg + 1, nShaderSize);

    AdminKeyID akid;
    Env::KeyID adminKid(&akid, sizeof(akid));

    uint32_t charge =
        Env::Cost::CallFar +
        Env::Cost::LoadVar_For(sizeof(Upgradable3::Settings)) +
        Env::Cost::AddSig +
        Env::Cost::SaveVar_For(nShaderSize + sizeof(Upgradable3::NextVersion)) +
        Env::Cost::Cycle * 500;

    Env::GenerateKernel(&cid, Upgradable3::Method::Control::s_iMethod,
        pArg, nArg, nullptr, 0, &adminKid, 1,
        "Idios: schedule upgrade", charge);

    Env::Heap_Free(pArg);
}

void On_manager_explicit_upgrade(const ContractID& cid)
{
    Upgradable3::Manager::MultiSigRitual::Perform_ExplicitUpgrade(cid);
}

// ----------------------------------------------------------------
//  User actions: Mode A job creation
// ----------------------------------------------------------------

void On_user_create_a(const ContractID& cid)
{
    Idios::CreateModeA args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("job_id",       &args.job_id))       return On_error("job_id required");
    if (!Env::DocGetNum64("subnet_id",    &args.subnet_id))    return On_error("subnet_id required");
    if (!Env::DocGetNum64("epoch",        &args.epoch))        return On_error("epoch required");
    if (!Env::DocGetNum64("expiry_block", &args.expiry_block)) return On_error("expiry_block required");
    if (!Env::DocGetNum64("payment",      &args.payment))      return On_error("payment required");
    if (!Env::DocGetNum32("asset_id",     &args.asset_id))     return On_error("asset_id required");
    if (!Env::DocGetBlob("node_pk",       &args.node_pk, sizeof(PubKey))) return On_error("node_pk required");
    if (!Env::DocGetBlob("result_hash",   args.result_hash, 32))           return On_error("result_hash required");
    Env::DocGetNum64("required_collateral", &args.required_collateral);
    Env::DocGetBlob("spec_hash", args.spec_hash, 32);

    UserKeyID kid;
    kid.m_Cid = cid;
    Env::DerivePk(args.requester_pk, &kid, sizeof(kid));
    Env::KeyID sigKid(&kid, sizeof(kid));

    FundsChange fc;
    fc.m_Amount  = args.payment;
    fc.m_Aid     = args.asset_id;
    fc.m_Consume = 1;

    Env::GenerateKernel(&cid, Idios::CreateModeA::s_iMethod,
        &args, sizeof(args), &fc, 1, &sigKid, 1,
        "Idios: create job (Mode A)",
        200000);
}

// ----------------------------------------------------------------
//  User actions: Mode B job creation
// ----------------------------------------------------------------

void On_user_create_b(const ContractID& cid)
{
    Idios::CreateModeB args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("job_id",               &args.job_id))               return On_error("job_id required");
    if (!Env::DocGetNum64("subnet_id",            &args.subnet_id))            return On_error("subnet_id required");
    if (!Env::DocGetNum64("epoch",                &args.epoch))                return On_error("epoch required");
    if (!Env::DocGetNum64("expiry_block",         &args.expiry_block))         return On_error("expiry_block required");
    Env::DocGetNum64("review_window_blocks", &args.review_window_blocks);
    if (!Env::DocGetNum64("payment",              &args.payment))              return On_error("payment required");
    if (!Env::DocGetNum64("dispute_fee",          &args.dispute_fee))          return On_error("dispute_fee required");
    if (!Env::DocGetNum32("asset_id",             &args.asset_id))             return On_error("asset_id required");
    if (!Env::DocGetBlob("node_pk",               &args.node_pk, sizeof(PubKey))) return On_error("node_pk required");
    Env::DocGetNum64("required_collateral", &args.required_collateral);
    Env::DocGetBlob("spec_hash", args.spec_hash, 32);

    UserKeyID kid;
    kid.m_Cid = cid;
    Env::DerivePk(args.requester_pk, &kid, sizeof(kid));
    Env::KeyID sigKid(&kid, sizeof(kid));

    FundsChange fc;
    fc.m_Amount  = args.payment;
    fc.m_Aid     = args.asset_id;
    fc.m_Consume = 1;

    Env::GenerateKernel(&cid, Idios::CreateModeB::s_iMethod,
        &args, sizeof(args), &fc, 1, &sigKid, 1,
        "Idios: create job (Mode B)",
        200000);
}

// ----------------------------------------------------------------
//  User actions: Bob commits collateral
// ----------------------------------------------------------------

void On_user_commit(const ContractID& cid)
{
    Idios::Commit args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("job_id",     &args.job_id))     return On_error("job_id required");
    if (!Env::DocGetNum64("collateral", &args.collateral)) return On_error("collateral required");

    Idios::KeyJob key;
    key.job_id = args.job_id;
    Env::Key_T<Idios::KeyJob> k;
    k.m_Prefix.m_Cid = cid;
    k.m_KeyInContract = key;
    Idios::Job job;
    if (!Env::VarReader::Read_T(k, job)) return On_error("Job not found");
    args.asset_id = job.asset_id;

    UserKeyID kid;
    kid.m_Cid = cid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    FundsChange fc;
    fc.m_Amount  = args.collateral;
    fc.m_Aid     = job.asset_id;
    fc.m_Consume = 1;

    Env::GenerateKernel(&cid, Idios::Commit::s_iMethod,
        &args, sizeof(args), &fc, 1, &sigKid, 1,
        "Idios: commit to job",
        200000);
}

// ----------------------------------------------------------------
//  User actions: Bob submits delivery (Mode A auto-settles, Mode B opens review)
// ----------------------------------------------------------------

void On_user_submit_delivery(const ContractID& cid)
{
    Idios::SubmitDelivery args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("job_id",        &args.job_id))                   return On_error("job_id required");
    if (!Env::DocGetBlob("delivery_hash",  args.delivery_hash, 32))         return On_error("delivery_hash required");

    Idios::KeyJob key;
    key.job_id = args.job_id;
    Env::Key_T<Idios::KeyJob> k;
    k.m_Prefix.m_Cid = cid;
    k.m_KeyInContract = key;
    Idios::Job job;
    if (!Env::VarReader::Read_T(k, job)) return On_error("Job not found");

    UserKeyID kid;
    kid.m_Cid = cid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    if (job.mode == 'A') {
        FundsChange fc;
        fc.m_Amount  = job.payment + job.collateral;
        fc.m_Aid     = job.asset_id;
        fc.m_Consume = 0;
        Env::GenerateKernel(&cid, Idios::SubmitDelivery::s_iMethod,
            &args, sizeof(args), &fc, 1, &sigKid, 1,
            "Idios: submit delivery (Mode A auto-settle)",
        200000);
    } else {
        Env::GenerateKernel(&cid, Idios::SubmitDelivery::s_iMethod,
            &args, sizeof(args), nullptr, 0, &sigKid, 1,
            "Idios: submit delivery (Mode B awaiting approval)",
        200000);
    }
}

// ----------------------------------------------------------------
//  User actions: Alice approves Mode B job
// ----------------------------------------------------------------

void On_user_approve(const ContractID& cid)
{
    Idios::Approve args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("job_id", &args.job_id)) return On_error("job_id required");

    UserKeyID kid;
    kid.m_Cid = cid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    Env::GenerateKernel(&cid, Idios::Approve::s_iMethod,
        &args, sizeof(args), nullptr, 0, &sigKid, 1,
        "Idios: approve delivery",
        200000);
}

void On_user_claim(const ContractID& cid)
{
    Idios::Claim args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("job_id", &args.job_id)) return On_error("job_id required");

    Idios::KeyJob key;
    key.job_id = args.job_id;
    Env::Key_T<Idios::KeyJob> k;
    k.m_Prefix.m_Cid = cid;
    k.m_KeyInContract = key;
    Idios::Job job;
    if (!Env::VarReader::Read_T(k, job)) return On_error("Job not found");
    if (job.mode == 'A') return On_error("Mode A jobs auto-settle, no claim needed");

    // v1 M of N: the winner takes payment + collateral. The dispute_fee is no
    // longer part of the winner's claim; it is the arbitration reward, claimed
    // by the consensus voters (arbitrator claim_reward) with the remainder
    // swept to treasury. This holds for Settled, ResolvedToAlice and
    // ResolvedToBob alike.
    uint64_t total = job.payment + job.collateral;

    UserKeyID kid;
    kid.m_Cid = cid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    FundsChange fc;
    fc.m_Amount  = total;
    fc.m_Aid     = job.asset_id;
    fc.m_Consume = 0;

    Env::GenerateKernel(&cid, Idios::Claim::s_iMethod,
        &args, sizeof(args), &fc, 1, &sigKid, 1,
        "Idios: claim settled funds",
        200000);
}

// ----------------------------------------------------------------
//  User actions: Alice raises dispute Mode B
// ----------------------------------------------------------------

void On_user_dispute(const ContractID& cid)
{
    Idios::Dispute args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("job_id", &args.job_id)) return On_error("job_id required");

    Idios::KeyJob key;
    key.job_id = args.job_id;
    Env::Key_T<Idios::KeyJob> k;
    k.m_Prefix.m_Cid = cid;
    k.m_KeyInContract = key;
    Idios::Job job;
    if (!Env::VarReader::Read_T(k, job)) return On_error("Job not found");

    UserKeyID kid;
    kid.m_Cid = cid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    FundsChange fc;
    fc.m_Amount  = job.dispute_fee;
    fc.m_Aid     = job.asset_id;
    fc.m_Consume = 1;

    Env::GenerateKernel(&cid, Idios::Dispute::s_iMethod,
        &args, sizeof(args), &fc, 1, &sigKid, 1,
        "Idios: dispute delivery",
        200000);
}

// ----------------------------------------------------------------
//  User actions: Bob claims after review timeout (Mode B)
// ----------------------------------------------------------------

void On_user_claim_after_timeout(const ContractID& cid)
{
    Idios::ClaimAfterTimeout args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("job_id", &args.job_id)) return On_error("job_id required");

    UserKeyID kid;
    kid.m_Cid = cid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    Env::GenerateKernel(&cid, Idios::ClaimAfterTimeout::s_iMethod,
        &args, sizeof(args), nullptr, 0, &sigKid, 1,
        "Idios: mark claimable after review timeout",
        200000);
}

// ----------------------------------------------------------------
//  User actions: Alice claims refund after expiry
// ----------------------------------------------------------------

void On_user_refund(const ContractID& cid)
{
    Idios::Refund args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("job_id", &args.job_id)) return On_error("job_id required");

    Idios::KeyJob key;
    key.job_id = args.job_id;
    Env::Key_T<Idios::KeyJob> k;
    k.m_Prefix.m_Cid = cid;
    k.m_KeyInContract = key;
    Idios::Job job;
    if (!Env::VarReader::Read_T(k, job)) return On_error("Job not found");

    UserKeyID kid;
    kid.m_Cid = cid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    FundsChange fc;
    fc.m_Amount  = job.payment;
    fc.m_Aid     = job.asset_id;
    fc.m_Consume = 0;

    Env::GenerateKernel(&cid, Idios::Refund::s_iMethod,
        &args, sizeof(args), &fc, 1, &sigKid, 1,
        "Idios: refund job",
        200000);
}

// ----------------------------------------------------------------
//  Arbitrator actions (M of N): register a bond
// ----------------------------------------------------------------

void On_arbitrator_register(const ContractID& cid)
{
    Idios::Register args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("stake", &args.stake)) return On_error("stake required");
    Env::DocGetNum32("asset_id", &args.asset_id);
    // v2 gates, pre checked here for clear errors; the contract enforces them
    if (args.asset_id != 0)                 return On_error("bond must be BEAM (asset 0)");
    if (args.stake < Idios::s_MinArbBond)   return On_error("stake below the 10 BEAM floor");

    uint32_t idx = 0;
    Env::DocGetNum32("arb_index", &idx);
    MofnArbKeyID kid;
    kid.m_Idx = idx;
    Env::DerivePk(args.arb_pk, &kid, sizeof(kid));

    // v2: the kernel carries the arbitrator signature AND the Upgradable3
    // admin signature (curation until an arbitrator slash exists). Both keys
    // in one wallet works (this is how tones registers his own indexes);
    // registering a genuinely external arbitrator needs a cross wallet dual
    // signature, the same limitation as mutual_cancel, and is exactly the
    // case the gate exists to curate.
    AdminKeyID akid;
    Env::KeyID pSig[2] = {
        Env::KeyID(&kid, sizeof(kid)),
        Env::KeyID(&akid, sizeof(akid)),
    };

    FundsChange fc;
    fc.m_Amount  = args.stake;
    fc.m_Aid     = args.asset_id;
    fc.m_Consume = 1; // lock the bond into the contract

    Env::GenerateKernel(&cid, Idios::Register::s_iMethod,
        &args, sizeof(args), &fc, 1, pSig, 2,
        "Idios: register arbitrator",
        200000);
}

void On_arbitrator_deregister(const ContractID& cid)
{
    Idios::Deregister args;
    Env::Memset(&args, 0, sizeof(args));

    uint32_t idx = 0;
    Env::DocGetNum32("arb_index", &idx);
    MofnArbKeyID kid;
    kid.m_Idx = idx;
    Env::DerivePk(args.arb_pk, &kid, sizeof(kid));
    Env::KeyID sigKid(&kid, sizeof(kid));

    Env::GenerateKernel(&cid, Idios::Deregister::s_iMethod,
        &args, sizeof(args), nullptr, 0, &sigKid, 1,
        "Idios: deregister arbitrator",
        200000);
}

void On_arbitrator_reclaim(const ContractID& cid)
{
    Idios::ReclaimStake args;
    Env::Memset(&args, 0, sizeof(args));

    uint32_t idx = 0;
    Env::DocGetNum32("arb_index", &idx);
    MofnArbKeyID kid;
    kid.m_Idx = idx;
    Env::DerivePk(args.arb_pk, &kid, sizeof(kid));
    Env::KeyID sigKid(&kid, sizeof(kid));

    // read the bond to know how much to unlock and in which asset
    Idios::KeyArb akey;
    Env::Memcpy(&akey.arb_pk, &args.arb_pk, sizeof(PubKey));
    Env::Key_T<Idios::KeyArb> k;
    k.m_Prefix.m_Cid = cid;
    k.m_KeyInContract = akey;
    Idios::ArbRec a;
    if (!Env::VarReader::Read_T(k, a)) return On_error("arbitrator not found");

    FundsChange fc;
    fc.m_Amount  = a.stake;
    fc.m_Aid     = a.asset_id;
    fc.m_Consume = 0; // unlock the bond back out

    Env::GenerateKernel(&cid, Idios::ReclaimStake::s_iMethod,
        &args, sizeof(args), &fc, 1, &sigKid, 1,
        "Idios: reclaim arbitrator bond",
        200000);
}

void On_arbitrator_vote(const ContractID& cid)
{
    Idios::Vote args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("job_id", &args.job_id)) return On_error("job_id required");
    uint32_t side = 0;
    if (!Env::DocGetNum32("side", &side)) return On_error("side required (0=Alice, 1=Bob)");
    if (side > 1) return On_error("side must be 0 (Alice) or 1 (Bob)");
    args.side = (uint8_t) side;

    uint32_t idx = 0;
    Env::DocGetNum32("arb_index", &idx);
    MofnArbKeyID kid;
    kid.m_Idx = idx;
    Env::DerivePk(args.arb_pk, &kid, sizeof(kid));
    Env::KeyID sigKid(&kid, sizeof(kid));

    // a vote moves no funds; the Mth matching vote only flips status
    Env::GenerateKernel(&cid, Idios::Vote::s_iMethod,
        &args, sizeof(args), nullptr, 0, &sigKid, 1,
        "Idios: cast arbitrator vote",
        400000);
}

void On_arbitrator_claim_reward(const ContractID& cid)
{
    Idios::ClaimArbReward args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("job_id", &args.job_id)) return On_error("job_id required");

    uint32_t idx = 0;
    Env::DocGetNum32("arb_index", &idx);
    MofnArbKeyID kid;
    kid.m_Idx = idx;
    Env::DerivePk(args.arb_pk, &kid, sizeof(kid));
    Env::KeyID sigKid(&kid, sizeof(kid));

    // need the job asset and the per voter share to set the FundsChange
    Idios::KeyJob jkey;
    jkey.job_id = args.job_id;
    Env::Key_T<Idios::KeyJob> jk;
    jk.m_Prefix.m_Cid = cid;
    jk.m_KeyInContract = jkey;
    Idios::Job job;
    if (!Env::VarReader::Read_T(jk, job)) return On_error("Job not found");

    Idios::KeyDispute dkey;
    dkey.job_id = args.job_id;
    Env::Key_T<Idios::KeyDispute> dk;
    dk.m_Prefix.m_Cid = cid;
    dk.m_KeyInContract = dkey;
    Idios::DisputeState ds;
    if (!Env::VarReader::Read_T(dk, ds)) return On_error("dispute not found");
    if (ds.resolution == 0) return On_error("dispute not resolved");

    FundsChange fc;
    fc.m_Amount  = ds.fee_share;
    fc.m_Aid     = job.asset_id;
    fc.m_Consume = 0; // unlock this voter's reward share

    Env::GenerateKernel(&cid, Idios::ClaimArbReward::s_iMethod,
        &args, sizeof(args), &fc, 1, &sigKid, 1,
        "Idios: claim arbitrator reward share",
        200000);
}

// ----------------------------------------------------------------
//  Worker bond actions (v2, reputation). The bond key is the worker's
//  UserKeyID, the same node key that takes jobs.
// ----------------------------------------------------------------

void On_user_worker_register(const ContractID& cid)
{
    Idios::WorkerRegister args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("stake", &args.stake)) return On_error("stake required");
    if (args.stake == 0)                         return On_error("stake must be > 0");
    args.asset_id = 0; // BEAM only, contract enforced

    UserKeyID kid;
    kid.m_Cid = cid;
    Env::DerivePk(args.worker_pk, &kid, sizeof(kid));
    Env::KeyID sigKid(&kid, sizeof(kid));

    FundsChange fc;
    fc.m_Amount  = args.stake;
    fc.m_Aid     = 0;
    fc.m_Consume = 1; // lock the bond into the contract

    Env::GenerateKernel(&cid, Idios::WorkerRegister::s_iMethod,
        &args, sizeof(args), &fc, 1, &sigKid, 1,
        "Idios: register worker bond",
        200000);
}

void On_user_worker_deregister(const ContractID& cid)
{
    Idios::WorkerDeregister args;
    Env::Memset(&args, 0, sizeof(args));

    UserKeyID kid;
    kid.m_Cid = cid;
    Env::DerivePk(args.worker_pk, &kid, sizeof(kid));
    Env::KeyID sigKid(&kid, sizeof(kid));

    Env::GenerateKernel(&cid, Idios::WorkerDeregister::s_iMethod,
        &args, sizeof(args), nullptr, 0, &sigKid, 1,
        "Idios: deregister worker bond",
        200000);
}

void On_user_worker_reclaim(const ContractID& cid)
{
    Idios::WorkerReclaim args;
    Env::Memset(&args, 0, sizeof(args));

    UserKeyID kid;
    kid.m_Cid = cid;
    Env::DerivePk(args.worker_pk, &kid, sizeof(kid));
    Env::KeyID sigKid(&kid, sizeof(kid));

    // read the bond to know how much to unlock
    Idios::KeyWorkerBond wkey;
    Env::Memcpy(&wkey.worker_pk, &args.worker_pk, sizeof(PubKey));
    Env::Key_T<Idios::KeyWorkerBond> k;
    k.m_Prefix.m_Cid = cid;
    k.m_KeyInContract = wkey;
    Idios::WorkerBondRec wb;
    if (!Env::VarReader::Read_T(k, wb)) return On_error("worker bond not found");
    if (wb.state == 3)                  return On_error("bond is slashed, cannot reclaim");
    if (wb.encumbrances != 0)           return On_error("bond encumbered by an open dispute");

    FundsChange fc;
    fc.m_Amount  = wb.stake;
    fc.m_Aid     = 0;
    fc.m_Consume = 0; // unlock the bond back out

    Env::GenerateKernel(&cid, Idios::WorkerReclaim::s_iMethod,
        &args, sizeof(args), &fc, 1, &sigKid, 1,
        "Idios: reclaim worker bond",
        200000);
}

// View a worker bond. worker_pk optional; defaults to the caller's own key,
// pass a pk to inspect someone else's bond (the score reader's path).
void On_user_view_worker_bond(const ContractID& cid)
{
    PubKey pk;
    if (!Env::DocGetBlob("worker_pk", &pk, sizeof(PubKey))) {
        UserKeyID kid;
        kid.m_Cid = cid;
        Env::DerivePk(pk, &kid, sizeof(kid));
    }

    Idios::KeyWorkerBond wkey;
    Env::Memcpy(&wkey.worker_pk, &pk, sizeof(PubKey));
    Env::Key_T<Idios::KeyWorkerBond> k;
    k.m_Prefix.m_Cid = cid;
    k.m_KeyInContract = wkey;
    Idios::WorkerBondRec wb;
    if (!Env::VarReader::Read_T(k, wb))
        return On_error("worker bond not found");

    Env::DocGroup gr("worker_bond");
    Env::DocAddBlob("worker_pk",      &pk, sizeof(PubKey));
    Env::DocAddNum64("stake",         wb.stake);
    Env::DocAddNum64("bonded_at",     wb.bonded_at);
    Env::DocAddNum64("dereg_block",   wb.dereg_block);
    Env::DocAddNum32("encumbrances",  wb.encumbrances);
    Env::DocAddNum32("state",         (uint32_t)wb.state);
}

// Treasury pulls a slashed bond (Method 29). Halts on chain unless the bond
// is slashed and every encumbered dispute has terminated (rule 125).
void On_treasury_slash_sweep(const ContractID& cid)
{
    Idios::SlashSweep args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetBlob("worker_pk", &args.worker_pk, sizeof(PubKey)))
        return On_error("worker_pk required");

    Idios::KeyWorkerBond wkey;
    Env::Memcpy(&wkey.worker_pk, &args.worker_pk, sizeof(PubKey));
    Env::Key_T<Idios::KeyWorkerBond> k;
    k.m_Prefix.m_Cid = cid;
    k.m_KeyInContract = wkey;
    Idios::WorkerBondRec wb;
    if (!Env::VarReader::Read_T(k, wb)) return On_error("worker bond not found");
    if (wb.state != 3)                  return On_error("bond is not slashed");
    if (wb.encumbrances != 0)           return On_error("open encumbered disputes remain, sweep must wait");

    TreasuryKeyID kid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    FundsChange fc;
    fc.m_Amount  = wb.stake;
    fc.m_Aid     = 0;
    fc.m_Consume = 0;

    Env::GenerateKernel(&cid, Idios::SlashSweep::s_iMethod,
        &args, sizeof(args), &fc, 1, &sigKid, 1,
        "Idios: treasury sweep slashed worker bond",
        200000);
}

// ----------------------------------------------------------------
//  Helper actions: get pubkeys
// ----------------------------------------------------------------

void On_user_get_key(const ContractID& cid)
{
    UserKeyID kid;
    kid.m_Cid = cid;
    PubKey pk;
    Env::DerivePk(pk, &kid, sizeof(kid));
    Env::DocGroup gr("key");
    Env::DocAddBlob("pub_key", &pk, sizeof(PubKey));
}

void On_arbitrator_get_key(const ContractID& cid)
{
    ArbitratorKeyID kid;
    PubKey pk;
    Env::DerivePk(pk, &kid, sizeof(kid));
    Env::DocGroup gr("key");
    Env::DocAddBlob("pub_key", &pk, sizeof(PubKey));
}

// M of N arbitrator key for a given index (default 0).
void On_arbitrator_get_mofn_key(const ContractID& cid)
{
    uint32_t idx = 0;
    Env::DocGetNum32("arb_index", &idx);
    MofnArbKeyID kid;
    kid.m_Idx = idx;
    PubKey pk;
    Env::DerivePk(pk, &kid, sizeof(kid));
    Env::DocGroup gr("key");
    Env::DocAddNum32("arb_index", idx);
    Env::DocAddBlob("pub_key", &pk, sizeof(PubKey));
}

void On_treasury_get_key(const ContractID& cid)
{
    TreasuryKeyID kid;
    PubKey pk;
    Env::DerivePk(pk, &kid, sizeof(kid));
    Env::DocGroup gr("key");
    Env::DocAddBlob("pub_key", &pk, sizeof(PubKey));
}

// ----------------------------------------------------------------
//  Arbitrator-timeout: void a stale dispute, then per-party claims
// ----------------------------------------------------------------

void On_user_void_dispute(const ContractID& cid)
{
    Idios::VoidStaleDispute args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("job_id", &args.job_id)) return On_error("job_id required");

    Env::GenerateKernel(&cid, Idios::VoidStaleDispute::s_iMethod,
        &args, sizeof(args), nullptr, 0, nullptr, 0,
        "Idios: void stale dispute (arbitrator timeout)",
        200000);
}

void On_user_void_claim_requester(const ContractID& cid)
{
    Idios::VoidClaimRequester args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("job_id", &args.job_id)) return On_error("job_id required");

    Idios::KeyJob key;
    key.job_id = args.job_id;
    Env::Key_T<Idios::KeyJob> k;
    k.m_Prefix.m_Cid = cid;
    k.m_KeyInContract = key;
    Idios::Job job;
    if (!Env::VarReader::Read_T(k, job)) return On_error("Job not found");

    UserKeyID kid;
    kid.m_Cid = cid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    FundsChange fc;
    fc.m_Amount  = job.payment;
    fc.m_Aid     = job.asset_id;
    fc.m_Consume = 0;

    Env::GenerateKernel(&cid, Idios::VoidClaimRequester::s_iMethod,
        &args, sizeof(args), &fc, 1, &sigKid, 1,
        "Idios: reclaim payment (voided dispute)",
        200000);
}

void On_user_void_claim_node(const ContractID& cid)
{
    Idios::VoidClaimNode args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("job_id", &args.job_id)) return On_error("job_id required");

    Idios::KeyJob key;
    key.job_id = args.job_id;
    Env::Key_T<Idios::KeyJob> k;
    k.m_Prefix.m_Cid = cid;
    k.m_KeyInContract = key;
    Idios::Job job;
    if (!Env::VarReader::Read_T(k, job)) return On_error("Job not found");

    UserKeyID kid;
    kid.m_Cid = cid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    FundsChange fc;
    fc.m_Amount  = job.collateral;
    fc.m_Aid     = job.asset_id;
    fc.m_Consume = 0;

    Env::GenerateKernel(&cid, Idios::VoidClaimNode::s_iMethod,
        &args, sizeof(args), &fc, 1, &sigKid, 1,
        "Idios: reclaim collateral (voided dispute)",
        200000);
}

// ----------------------------------------------------------------
//  Treasury: sweep forfeited funds (collateral Refunded, fee Voided,
//  reward remainder Resolved)
// ----------------------------------------------------------------

void On_treasury_sweep(const ContractID& cid)
{
    Idios::TreasurySweep args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("job_id", &args.job_id)) return On_error("job_id required");

    Idios::KeyJob key;
    key.job_id = args.job_id;
    Env::Key_T<Idios::KeyJob> k;
    k.m_Prefix.m_Cid = cid;
    k.m_KeyInContract = key;
    Idios::Job job;
    if (!Env::VarReader::Read_T(k, job)) return On_error("Job not found");

    uint64_t amount = 0;
    if ((uint32_t)job.status == Idios::JobStatus::Refunded) {
        amount = job.collateral;
    } else if ((uint32_t)job.status == Idios::JobStatus::Voided) {
        amount = job.dispute_fee;
    } else if ((uint32_t)job.status == Idios::JobStatus::ResolvedToAlice ||
               (uint32_t)job.status == Idios::JobStatus::ResolvedToBob) {
        // M of N reward remainder F % M
        Idios::KeyDispute dkey;
        dkey.job_id = args.job_id;
        Env::Key_T<Idios::KeyDispute> dk;
        dk.m_Prefix.m_Cid = cid;
        dk.m_KeyInContract = dkey;
        Idios::DisputeState ds;
        if (Env::VarReader::Read_T(dk, ds) && ds.resolution != 0 && !ds.remainder_swept)
            amount = ds.fee_remainder;
    }
    if (amount == 0) return On_error("nothing to sweep for this job");

    TreasuryKeyID kid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    FundsChange fc;
    fc.m_Amount  = amount;
    fc.m_Aid     = job.asset_id;
    fc.m_Consume = 0;

    Env::GenerateKernel(&cid, Idios::TreasurySweep::s_iMethod,
        &args, sizeof(args), &fc, 1, &sigKid, 1,
        "Idios: treasury sweep forfeited funds",
        200000);
}

// ----------------------------------------------------------------
//  User actions: mutual cancel (v5, Method 20)
// ----------------------------------------------------------------

void On_user_mutual_cancel(const ContractID& cid)
{
    Idios::MutualCancel args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("job_id", &args.job_id)) return On_error("job_id required");

    Idios::KeyJob key;
    key.job_id = args.job_id;
    Env::Key_T<Idios::KeyJob> k;
    k.m_Prefix.m_Cid = cid;
    k.m_KeyInContract = key;
    Idios::Job job;
    if (!Env::VarReader::Read_T(k, job)) return On_error("Job not found");

    UserKeyID kid;
    kid.m_Cid = cid;
    Env::KeyID pSig[2] = {
        Env::KeyID(&kid, sizeof(kid)),
        Env::KeyID(&kid, sizeof(kid)),
    };

    FundsChange fc;
    fc.m_Amount  = job.payment + job.collateral;
    fc.m_Aid     = job.asset_id;
    fc.m_Consume = 0;

    Env::GenerateKernel(&cid, Idios::MutualCancel::s_iMethod,
        &args, sizeof(args), &fc, 1, pSig, 2,
        "Idios: mutual cancel (both parties, everyone whole)",
        200000);
}

// ----------------------------------------------------------------
//  View job
// ----------------------------------------------------------------

void On_user_view_job(const ContractID& cid)
{
    uint64_t job_id = 0;
    if (!Env::DocGetNum64("job_id", &job_id)) return On_error("job_id required");

    Idios::KeyJob key;
    key.job_id = job_id;
    Env::Key_T<Idios::KeyJob> k;
    k.m_Prefix.m_Cid = cid;
    k.m_KeyInContract = key;

    Idios::Job job;
    if (!Env::VarReader::Read_T(k, job))
        return On_error("Job not found");

    Env::DocGroup gr("job");
    Env::DocAddNum64("job_id",                job.job_id);
    Env::DocAddNum64("subnet_id",             job.subnet_id);
    Env::DocAddNum64("payment",               job.payment);
    Env::DocAddNum64("collateral",            job.collateral);
    Env::DocAddNum64("dispute_fee",           job.dispute_fee);
    Env::DocAddNum64("required_collateral",   job.required_collateral);
    Env::DocAddNum32("asset_id",              job.asset_id);
    Env::DocAddNum32("status",                (uint32_t)job.status);
    Env::DocAddNum32("mode",                  (uint32_t)job.mode);
    Env::DocAddNum64("expiry_block",          job.expiry_block);
    Env::DocAddNum64("review_window_blocks",  job.review_window_blocks);
    Env::DocAddNum64("review_deadline_block", job.review_deadline_block);
    Env::DocAddNum64("dispute_filed_block",   job.dispute_filed_block);
    Env::DocAddBlob("node_pk",                &job.node_pk, sizeof(PubKey));
    Env::DocAddBlob("requester_pk",           &job.requester_pk, sizeof(PubKey));
    Env::DocAddBlob("result_hash",            job.result_hash, 32);
    Env::DocAddBlob("delivery_hash",          job.delivery_hash, 32);
    Env::DocAddBlob("spec_hash",              job.spec_hash, 32);
}

// View the per dispute M of N state (frozen N/M, tallies, resolution).
void On_user_view_dispute(const ContractID& cid)
{
    uint64_t job_id = 0;
    if (!Env::DocGetNum64("job_id", &job_id)) return On_error("job_id required");

    Idios::KeyDispute key;
    key.job_id = job_id;
    Env::Key_T<Idios::KeyDispute> k;
    k.m_Prefix.m_Cid = cid;
    k.m_KeyInContract = key;

    Idios::DisputeState ds;
    if (!Env::VarReader::Read_T(k, ds))
        return On_error("Dispute not found");

    Env::DocGroup gr("dispute");
    Env::DocAddNum64("frozen_n",        ds.frozen_n);
    Env::DocAddNum32("threshold",       ds.threshold);
    Env::DocAddNum32("vc_alice",        ds.vc_alice);
    Env::DocAddNum32("vc_bob",          ds.vc_bob);
    Env::DocAddNum64("fee_share",       ds.fee_share);
    Env::DocAddNum64("fee_remainder",   ds.fee_remainder);
    Env::DocAddNum32("resolution",      (uint32_t)ds.resolution);
    Env::DocAddNum32("winner_paid",     (uint32_t)ds.winner_paid);
    Env::DocAddNum32("remainder_swept", (uint32_t)ds.remainder_swept);
    Env::DocAddNum32("bond_encumbered", (uint32_t)ds.bond_encumbered);
}

// View an arbitrator's bond and registry state by index.
void On_arbitrator_view_arb(const ContractID& cid)
{
    uint32_t idx = 0;
    Env::DocGetNum32("arb_index", &idx);
    MofnArbKeyID akid;
    akid.m_Idx = idx;
    PubKey pk;
    Env::DerivePk(pk, &akid, sizeof(akid));

    Idios::KeyArb key;
    Env::Memcpy(&key.arb_pk, &pk, sizeof(PubKey));
    Env::Key_T<Idios::KeyArb> k;
    k.m_Prefix.m_Cid = cid;
    k.m_KeyInContract = key;

    Idios::ArbRec a;
    if (!Env::VarReader::Read_T(k, a))
        return On_error("arbitrator not found");

    Env::DocGroup gr("arb");
    Env::DocAddNum32("arb_index",     idx);
    Env::DocAddBlob("pub_key",        &pk, sizeof(PubKey));
    Env::DocAddNum64("stake",         a.stake);
    Env::DocAddNum32("asset_id",      a.asset_id);
    Env::DocAddNum64("registered_at", a.registered_at);
    Env::DocAddNum64("dereg_block",   a.dereg_block);
    Env::DocAddNum32("state",         (uint32_t)a.state);
}

// ----------------------------------------------------------------
//  Method_0: schema export
// ----------------------------------------------------------------

BEAM_EXPORT void Method_0()
{
    Env::DocGroup root("");
    {
        Env::DocGroup gr("roles");
        {
            Env::DocGroup grRole("manager");
            { Env::DocGroup grMethod("deploy"); }
            { Env::DocGroup grMethod("view"); }
            { Env::DocGroup grMethod("view_regcount"); }
            {
                Env::DocGroup grMethod("view_job");
                Env::DocAddText("job_id", "uint64");
            }
            {
                Env::DocGroup grMethod("view_dispute");
                Env::DocAddText("job_id", "uint64");
            }
            { Env::DocGroup grMethod("schedule_upgrade"); }
            { Env::DocGroup grMethod("explicit_upgrade"); }
        }
        {
            Env::DocGroup grRole("user");
            {
                Env::DocGroup grMethod("create_a");
                Env::DocAddText("job_id",       "uint64");
                Env::DocAddText("subnet_id",    "uint64");
                Env::DocAddText("epoch",        "uint64");
                Env::DocAddText("expiry_block", "uint64");
                Env::DocAddText("payment",      "Amount");
                Env::DocAddText("required_collateral", "Amount (optional, 0=no floor)");
                Env::DocAddText("asset_id",     "AssetID");
                Env::DocAddText("node_pk",      "PubKey");
                Env::DocAddText("result_hash",  "blob32");
                Env::DocAddText("spec_hash",    "blob32 (optional)");
            }
            {
                Env::DocGroup grMethod("create_b");
                Env::DocAddText("job_id",               "uint64");
                Env::DocAddText("subnet_id",            "uint64");
                Env::DocAddText("epoch",                "uint64");
                Env::DocAddText("expiry_block",         "uint64");
                Env::DocAddText("review_window_blocks", "uint64 (optional, 0=contract default)");
                Env::DocAddText("payment",              "Amount");
                Env::DocAddText("dispute_fee",          "Amount");
                Env::DocAddText("required_collateral",  "Amount (optional, 0=no floor)");
                Env::DocAddText("asset_id",             "AssetID");
                Env::DocAddText("node_pk",              "PubKey");
                Env::DocAddText("spec_hash",            "blob32 (optional)");
            }
            {
                Env::DocGroup grMethod("commit");
                Env::DocAddText("job_id",     "uint64");
                Env::DocAddText("collateral", "Amount");
                Env::DocAddText("asset_id",   "AssetID");
            }
            {
                Env::DocGroup grMethod("submit_delivery");
                Env::DocAddText("job_id",        "uint64");
                Env::DocAddText("delivery_hash", "blob32");
            }
            {
                Env::DocGroup grMethod("approve");
                Env::DocAddText("job_id",     "uint64");
            }
            {
                Env::DocGroup grMethod("dispute");
                Env::DocAddText("job_id",      "uint64");
            }
            {
                Env::DocGroup grMethod("claim_after_timeout");
                Env::DocAddText("job_id",     "uint64");
            }
            {
                Env::DocGroup grMethod("refund");
                Env::DocAddText("job_id", "uint64");
            }
            {
                Env::DocGroup grMethod("claim");
                Env::DocAddText("job_id",   "uint64");
            }
            {
                Env::DocGroup grMethod("mutual_cancel");
                Env::DocAddText("job_id", "uint64");
            }
            {
                Env::DocGroup grMethod("void_dispute");
                Env::DocAddText("job_id", "uint64");
            }
            {
                Env::DocGroup grMethod("void_claim_requester");
                Env::DocAddText("job_id", "uint64");
            }
            {
                Env::DocGroup grMethod("void_claim_node");
                Env::DocAddText("job_id", "uint64");
            }
            {
                Env::DocGroup grMethod("worker_register");
                Env::DocAddText("stake", "Amount (BEAM only, slashable reputation bond)");
            }
            { Env::DocGroup grMethod("worker_deregister"); }
            { Env::DocGroup grMethod("worker_reclaim"); }
            {
                Env::DocGroup grMethod("view_worker_bond");
                Env::DocAddText("worker_pk", "PubKey (optional, defaults to own key)");
            }
        }
        {
            Env::DocGroup grRole("arbitrator");
            {
                Env::DocGroup grMethod("register");
                Env::DocAddText("stake",     "Amount (BEAM only, min 10 BEAM, needs admin co sign)");
                Env::DocAddText("arb_index", "uint32 (optional, 0; distinct keys per index)");
            }
            {
                Env::DocGroup grMethod("deregister");
                Env::DocAddText("arb_index", "uint32 (optional, 0)");
            }
            {
                Env::DocGroup grMethod("reclaim");
                Env::DocAddText("arb_index", "uint32 (optional, 0)");
            }
            {
                Env::DocGroup grMethod("vote");
                Env::DocAddText("job_id",    "uint64");
                Env::DocAddText("side",      "uint32 (0=Alice, 1=Bob)");
                Env::DocAddText("arb_index", "uint32 (optional, 0)");
            }
            {
                Env::DocGroup grMethod("claim_reward");
                Env::DocAddText("job_id",    "uint64");
                Env::DocAddText("arb_index", "uint32 (optional, 0)");
            }
            {
                Env::DocGroup grMethod("get_mofn_key");
                Env::DocAddText("arb_index", "uint32 (optional, 0)");
            }
            {
                Env::DocGroup grMethod("view_arb");
                Env::DocAddText("arb_index", "uint32 (optional, 0)");
            }
        }
        {
            Env::DocGroup grRole("treasury");
            {
                Env::DocGroup grMethod("sweep");
                Env::DocAddText("job_id", "uint64");
            }
            {
                Env::DocGroup grMethod("slash_sweep");
                Env::DocAddText("worker_pk", "PubKey (slashed bond to pull)");
            }
        }
    }
}

// ----------------------------------------------------------------
//  User actions: batch create Mode B contracts (POC, CLI only)
// ----------------------------------------------------------------
void On_user_batch_create_b(const ContractID& cid)
{
    static const uint32_t nMaxCount = 50;

    uint32_t batch_count = 0;
    if (!Env::DocGetNum32("batch_count", &batch_count)) return On_error("batch_count required");
    if (batch_count == 0)                               return On_error("batch_count must be > 0");
    if (batch_count > nMaxCount)                        return On_error("batch_count exceeds max 50");

    UserKeyID kid;
    kid.m_Cid = cid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    for (uint32_t i = 0; i < batch_count; i++)
    {
        Idios::CreateModeB args;
        Env::Memset(&args, 0, sizeof(args));

        auto k_job_id               = Utils::MakeFieldIndex<50>("job_id_");
        auto k_subnet_id            = Utils::MakeFieldIndex<50>("subnet_id_");
        auto k_epoch                = Utils::MakeFieldIndex<50>("epoch_");
        auto k_expiry_block         = Utils::MakeFieldIndex<50>("expiry_block_");
        auto k_review_window_blocks = Utils::MakeFieldIndex<50>("review_window_blocks_");
        auto k_payment              = Utils::MakeFieldIndex<50>("payment_");
        auto k_dispute_fee          = Utils::MakeFieldIndex<50>("dispute_fee_");
        auto k_asset_id             = Utils::MakeFieldIndex<50>("asset_id_");
        auto k_node_pk              = Utils::MakeFieldIndex<50>("node_pk_");

        k_job_id.Set(i);
        k_subnet_id.Set(i);
        k_epoch.Set(i);
        k_expiry_block.Set(i);
        k_review_window_blocks.Set(i);
        k_payment.Set(i);
        k_dispute_fee.Set(i);
        k_asset_id.Set(i);
        k_node_pk.Set(i);

        if (!Env::DocGetNum64(k_job_id.m_sz,               &args.job_id))               return On_error("job_id required");
        if (!Env::DocGetNum64(k_subnet_id.m_sz,            &args.subnet_id))            return On_error("subnet_id required");
        if (!Env::DocGetNum64(k_epoch.m_sz,                &args.epoch))                return On_error("epoch required");
        if (!Env::DocGetNum64(k_expiry_block.m_sz,         &args.expiry_block))         return On_error("expiry_block required");
        if (!Env::DocGetNum64(k_review_window_blocks.m_sz, &args.review_window_blocks)) return On_error("review_window_blocks required");
        if (!Env::DocGetNum64(k_payment.m_sz,              &args.payment))              return On_error("payment required");
        if (!Env::DocGetNum64(k_dispute_fee.m_sz,          &args.dispute_fee))          return On_error("dispute_fee required");
        if (!Env::DocGetNum32(k_asset_id.m_sz,             &args.asset_id))             return On_error("asset_id required");
        if (!Env::DocGetBlob(k_node_pk.m_sz,               &args.node_pk, sizeof(PubKey))) return On_error("node_pk required");

        Env::DerivePk(args.requester_pk, &kid, sizeof(kid));

        FundsChange fc;
        fc.m_Amount  = args.payment;
        fc.m_Aid     = args.asset_id;
        fc.m_Consume = 1;

        Env::GenerateKernel(&cid, Idios::CreateModeB::s_iMethod,
            &args, sizeof(args), &fc, 1, &sigKid, 1,
            i == 0 ? "Idios: batch create contracts (Mode B)" : "",
            200000);
    }
}

// ----------------------------------------------------------------
//  Method_1: dispatch
// ----------------------------------------------------------------

BEAM_EXPORT void Method_1()
{
    static const ActionEntry MANAGER_ACTIONS[] = {
        {"deploy",   On_manager_deploy},
        {"create",   On_manager_deploy},
        {"view",     On_manager_view},
        {"view_regcount", On_manager_view_regcount},
        {"view_job", On_user_view_job},
        {"view_dispute", On_user_view_dispute},
        {"schedule_upgrade", On_manager_schedule_upgrade},
        {"explicit_upgrade", On_manager_explicit_upgrade},
    };
    static const ActionEntry USER_ACTIONS[] = {
        {"create_a",            On_user_create_a},
        {"create_b",            On_user_create_b},
        {"create",              On_user_create_a},
        {"commit",              On_user_commit},
        {"submit_delivery",     On_user_submit_delivery},
        {"approve",             On_user_approve},
        {"dispute",             On_user_dispute},
        {"claim_after_timeout", On_user_claim_after_timeout},
        {"refund",              On_user_refund},
        {"claim",               On_user_claim},
        {"mutual_cancel",         On_user_mutual_cancel},
        {"void_dispute",          On_user_void_dispute},
        {"void_claim_requester",  On_user_void_claim_requester},
        {"void_claim_node",       On_user_void_claim_node},
        {"get_key",             On_user_get_key},
        {"view_job",            On_user_view_job},
        {"view_dispute",        On_user_view_dispute},
        {"worker_register",     On_user_worker_register},
        {"worker_deregister",   On_user_worker_deregister},
        {"worker_reclaim",      On_user_worker_reclaim},
        {"view_worker_bond",    On_user_view_worker_bond},
        {"batch_create_b",      On_user_batch_create_b},
    };
    static const ActionEntry ARBITRATOR_ACTIONS[] = {
        {"register",     On_arbitrator_register},
        {"deregister",   On_arbitrator_deregister},
        {"reclaim",      On_arbitrator_reclaim},
        {"vote",         On_arbitrator_vote},
        {"claim_reward", On_arbitrator_claim_reward},
        {"get_key",      On_arbitrator_get_key},
        {"get_mofn_key", On_arbitrator_get_mofn_key},
        {"view_arb",     On_arbitrator_view_arb},
    };
    static const ActionEntry TREASURY_ACTIONS[] = {
        {"sweep",       On_treasury_sweep},
        {"slash_sweep", On_treasury_slash_sweep},
        {"get_key",     On_treasury_get_key},
    };
    static const RoleEntry VALID_ROLES[] = {
        {"manager",    MANAGER_ACTIONS,    sizeof(MANAGER_ACTIONS) / sizeof(MANAGER_ACTIONS[0])},
        {"user",       USER_ACTIONS,       sizeof(USER_ACTIONS) / sizeof(USER_ACTIONS[0])},
        {"arbitrator", ARBITRATOR_ACTIONS, sizeof(ARBITRATOR_ACTIONS) / sizeof(ARBITRATOR_ACTIONS[0])},
        {"treasury",   TREASURY_ACTIONS,   sizeof(TREASURY_ACTIONS) / sizeof(TREASURY_ACTIONS[0])},
    };
    static const uint32_t ROLE_COUNT = sizeof(VALID_ROLES) / sizeof(VALID_ROLES[0]);

    char action[ACTION_BUF_SIZE], role[ROLE_BUF_SIZE];

    if (!Env::DocGetText("role", role, sizeof(role)))
        return On_error("role required");

    const RoleEntry* found_role = nullptr;
    for (uint32_t i = 0; i < ROLE_COUNT; i++) {
        if (str_eq(role, VALID_ROLES[i].name)) {
            found_role = &VALID_ROLES[i];
            break;
        }
    }
    if (!found_role)
        return On_error("invalid role");

    if (!Env::DocGetText("action", action, sizeof(action)))
        return On_error("action required");

    Action_func_t found_handler = nullptr;
    for (uint32_t i = 0; i < found_role->action_count; i++) {
        if (str_eq(action, found_role->actions[i].name)) {
            found_handler = found_role->actions[i].handler;
            break;
        }
    }
    if (!found_handler)
        return On_error("invalid action");

    ContractID cid;
    Env::DocGet("cid", cid);
    found_handler(cid);
}

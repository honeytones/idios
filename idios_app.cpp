#include "Shaders/common.h"
#include "Shaders/app_common_impl.h"
#include "idios_contract.h"

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

// ----------------------------------------------------------------
//  Manager actions (deploy, view)
// ----------------------------------------------------------------

void On_manager_deploy(const ContractID& unused)
{
    Idios::Params params;
    Env::Memset(&params, 0, sizeof(params));

    ArbitratorKeyID kid;
    Env::DerivePk(params.arbitrator_pk, &kid, sizeof(kid));

    uint64_t review_window = 10080;
    uint64_t arbitrator_timeout = 20160;
    Env::DocGetNum64("default_review_window", &review_window);
    Env::DocGetNum64("arbitrator_timeout_blocks", &arbitrator_timeout);
    params.default_review_window = review_window;
    params.arbitrator_timeout_blocks = arbitrator_timeout;

    Env::GenerateKernel(nullptr, 0, &params, sizeof(params),
        nullptr, 0, nullptr, 0, "Deploy Idios v2 contract", 0);
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
    Env::DocAddNum64("default_review_window", params.default_review_window);
    Env::DocAddNum64("arbitrator_timeout_blocks", params.arbitrator_timeout_blocks);
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
        "Idios: create job (Mode A)", 0);
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
    if (!Env::DocGetNum64("review_window_blocks", &args.review_window_blocks)) return On_error("review_window_blocks required");
    if (!Env::DocGetNum64("payment",              &args.payment))              return On_error("payment required");
    if (!Env::DocGetNum64("dispute_fee",          &args.dispute_fee))          return On_error("dispute_fee required");
    if (!Env::DocGetNum32("asset_id",             &args.asset_id))             return On_error("asset_id required");
    if (!Env::DocGetBlob("node_pk",               &args.node_pk, sizeof(PubKey))) return On_error("node_pk required");

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
        "Idios: create job (Mode B)", 0);
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
    if (!Env::DocGetNum32("asset_id",   &args.asset_id))   return On_error("asset_id required");

    UserKeyID kid;
    kid.m_Cid = cid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    FundsChange fc;
    fc.m_Amount  = args.collateral;
    fc.m_Aid     = args.asset_id;
    fc.m_Consume = 1;

    Env::GenerateKernel(&cid, Idios::Commit::s_iMethod,
        &args, sizeof(args), &fc, 1, &sigKid, 1,
        "Idios: commit to job", 0);
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

    uint64_t payment = 0, collateral = 0;
    uint32_t asset_id = 0;
    uint32_t mode = 0;
    Env::DocGetNum64("payment", &payment);
    Env::DocGetNum64("collateral", &collateral);
    Env::DocGetNum32("asset_id", &asset_id);
    Env::DocGetNum32("mode", &mode);

    UserKeyID kid;
    kid.m_Cid = cid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    if (mode == 'A') {
        FundsChange fc;
        fc.m_Amount  = payment + collateral;
        fc.m_Aid     = asset_id;
        fc.m_Consume = 0;
        Env::GenerateKernel(&cid, Idios::SubmitDelivery::s_iMethod,
            &args, sizeof(args), &fc, 1, &sigKid, 1,
            "Idios: submit delivery (Mode A auto-settle)", 0);
    } else {
        Env::GenerateKernel(&cid, Idios::SubmitDelivery::s_iMethod,
            &args, sizeof(args), nullptr, 0, &sigKid, 1,
            "Idios: submit delivery (Mode B awaiting approval)", 0);
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
        "Idios: approve delivery", 0);
}

void On_user_claim(const ContractID& cid)
{
    Idios::Claim args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("job_id", &args.job_id)) return On_error("job_id required");

    uint64_t total = 0;
    uint32_t asset_id = 0;
    Env::DocGetNum64("total", &total);
    Env::DocGetNum32("asset_id", &asset_id);

    UserKeyID kid;
    kid.m_Cid = cid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    FundsChange fc;
    fc.m_Amount  = total;
    fc.m_Aid     = asset_id;
    fc.m_Consume = 0;

    Env::GenerateKernel(&cid, Idios::Claim::s_iMethod,
        &args, sizeof(args), &fc, 1, &sigKid, 1,
        "Idios: claim settled funds", 0);
}

// ----------------------------------------------------------------
//  User actions: Alice raises dispute Mode B
// ----------------------------------------------------------------

void On_user_dispute(const ContractID& cid)
{
    Idios::Dispute args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("job_id", &args.job_id)) return On_error("job_id required");

    uint64_t dispute_fee = 0;
    uint32_t asset_id = 0;
    Env::DocGetNum64("dispute_fee", &dispute_fee);
    Env::DocGetNum32("asset_id", &asset_id);

    UserKeyID kid;
    kid.m_Cid = cid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    FundsChange fc;
    fc.m_Amount  = dispute_fee;
    fc.m_Aid     = asset_id;
    fc.m_Consume = 1;

    Env::GenerateKernel(&cid, Idios::Dispute::s_iMethod,
        &args, sizeof(args), &fc, 1, &sigKid, 1,
        "Idios: dispute delivery", 0);
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
        "Idios: mark claimable after review timeout", 0);
}

// ----------------------------------------------------------------
//  User actions: Alice claims refund after expiry
// ----------------------------------------------------------------

void On_user_refund(const ContractID& cid)
{
    Idios::Refund args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("job_id", &args.job_id)) return On_error("job_id required");

    uint64_t payment = 0, collateral = 0;
    uint32_t asset_id = 0;
    Env::DocGetNum64("payment", &payment);
    Env::DocGetNum64("collateral", &collateral);
    Env::DocGetNum32("asset_id", &asset_id);

    UserKeyID kid;
    kid.m_Cid = cid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    FundsChange fc;
    fc.m_Amount  = payment + collateral;
    fc.m_Aid     = asset_id;
    fc.m_Consume = 0;

    Env::GenerateKernel(&cid, Idios::Refund::s_iMethod,
        &args, sizeof(args), &fc, 1, &sigKid, 1,
        "Idios: refund job", 0);
}

// ----------------------------------------------------------------
//  Arbitrator actions: resolve dispute to Alice (winner takes all)
// ----------------------------------------------------------------

void On_arbitrator_resolve_alice(const ContractID& cid)
{
    Idios::ResolveToAlice args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("job_id", &args.job_id)) return On_error("job_id required");

    ArbitratorKeyID kid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    Env::GenerateKernel(&cid, Idios::ResolveToAlice::s_iMethod,
        &args, sizeof(args), nullptr, 0, &sigKid, 1,
        "Idios: resolve dispute to Alice", 0);
}

// ----------------------------------------------------------------
//  Arbitrator actions: resolve dispute to Bob (winner takes all)
// ----------------------------------------------------------------

void On_arbitrator_resolve_bob(const ContractID& cid)
{
    Idios::ResolveToBob args;
    Env::Memset(&args, 0, sizeof(args));
    if (!Env::DocGetNum64("job_id", &args.job_id)) return On_error("job_id required");

    ArbitratorKeyID kid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    Env::GenerateKernel(&cid, Idios::ResolveToBob::s_iMethod,
        &args, sizeof(args), nullptr, 0, &sigKid, 1,
        "Idios: resolve dispute to Bob", 0);
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

// ----------------------------------------------------------------
//  View job
// ----------------------------------------------------------------

void On_user_view_job(const ContractID& cid)
{
    uint64_t job_id = 0;
    if (!Env::DocGetNum64("job_id", &job_id)) return On_error("job_id required");

    struct KeyJob {
        uint8_t  prefix = Idios::Tags::s_Job;
        uint64_t job_id;
    } key;
    key.job_id = job_id;
    Env::Key_T<KeyJob> k;
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
                Env::DocAddText("asset_id",     "AssetID");
                Env::DocAddText("node_pk",      "PubKey");
                Env::DocAddText("result_hash",  "blob32");
            }
            {
                Env::DocGroup grMethod("create_b");
                Env::DocAddText("job_id",               "uint64");
                Env::DocAddText("subnet_id",            "uint64");
                Env::DocAddText("epoch",                "uint64");
                Env::DocAddText("expiry_block",         "uint64");
                Env::DocAddText("review_window_blocks", "uint64");
                Env::DocAddText("payment",              "Amount");
                Env::DocAddText("dispute_fee",          "Amount");
                Env::DocAddText("asset_id",             "AssetID");
                Env::DocAddText("node_pk",              "PubKey");
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
                Env::DocAddText("mode",          "uint32");
                Env::DocAddText("payment",       "Amount");
                Env::DocAddText("collateral",    "Amount");
                Env::DocAddText("asset_id",      "AssetID");
            }
            {
                Env::DocGroup grMethod("approve");
                Env::DocAddText("job_id",     "uint64");
                Env::DocAddText("payment",    "Amount");
                Env::DocAddText("collateral", "Amount");
                Env::DocAddText("asset_id",   "AssetID");
            }
            {
                Env::DocGroup grMethod("dispute");
                Env::DocAddText("job_id",      "uint64");
                Env::DocAddText("dispute_fee", "Amount");
                Env::DocAddText("asset_id",    "AssetID");
            }
            {
                Env::DocGroup grMethod("claim_after_timeout");
                Env::DocAddText("job_id",     "uint64");
                Env::DocAddText("payment",    "Amount");
                Env::DocAddText("collateral", "Amount");
                Env::DocAddText("asset_id",   "AssetID");
            }
            {
                Env::DocGroup grMethod("refund");
                Env::DocAddText("job_id", "uint64");
            }
            {
                Env::DocGroup grMethod("claim");
                Env::DocAddText("job_id",   "uint64");
                Env::DocAddText("total",    "Amount");
                Env::DocAddText("asset_id", "AssetID");
            }
            {
                Env::DocGroup grMethod("view_job");
                Env::DocAddText("job_id", "uint64");
            }
        }
        {
            Env::DocGroup grRole("arbitrator");
            {
                Env::DocGroup grMethod("resolve_alice");
                Env::DocAddText("job_id",   "uint64");
                Env::DocAddText("total",    "Amount");
                Env::DocAddText("asset_id", "AssetID");
            }
            {
                Env::DocGroup grMethod("resolve_bob");
                Env::DocAddText("job_id",   "uint64");
                Env::DocAddText("total",    "Amount");
                Env::DocAddText("asset_id", "AssetID");
            }
        }
    }
}

// ----------------------------------------------------------------
//  Method_1: dispatch
// ----------------------------------------------------------------

BEAM_EXPORT void Method_1()
{
    static const ActionEntry MANAGER_ACTIONS[] = {
        {"deploy", On_manager_deploy},
        {"create", On_manager_deploy},
        {"view",   On_manager_view},
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
        {"view_job",            On_user_view_job},
        {"get_key",             On_user_get_key},
    };
    static const ActionEntry ARBITRATOR_ACTIONS[] = {
        {"resolve_alice", On_arbitrator_resolve_alice},
        {"resolve_bob",   On_arbitrator_resolve_bob},
        {"get_key",       On_arbitrator_get_key},
    };
    static const RoleEntry VALID_ROLES[] = {
        {"manager",    MANAGER_ACTIONS,    sizeof(MANAGER_ACTIONS) / sizeof(MANAGER_ACTIONS[0])},
        {"user",       USER_ACTIONS,       sizeof(USER_ACTIONS) / sizeof(USER_ACTIONS[0])},
        {"arbitrator", ARBITRATOR_ACTIONS, sizeof(ARBITRATOR_ACTIONS) / sizeof(ARBITRATOR_ACTIONS[0])},
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

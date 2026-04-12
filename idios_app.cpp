#include "Shaders/common.h"
#include <algorithm>
#include <vector>
#include <utility>
#include <string_view>
#include "Shaders/app_common_impl.h"
#include "contract.h"

using Action_func_t = void (*)(const ContractID&);
using Actions_map_t = std::vector<std::pair<std::string_view, Action_func_t>>;
using Roles_map_t = std::vector<std::pair<std::string_view, const Actions_map_t&>>;

constexpr size_t ACTION_BUF_SIZE = 32;
constexpr size_t ROLE_BUF_SIZE = 16;

void On_error(const char* msg)
{
    Env::DocGroup root("");
    Env::DocAddText("error", msg);
}

template <typename T>
auto find_if_contains(const std::string_view str, const std::vector<std::pair<std::string_view, T>>& v)
{
    return std::find_if(v.begin(), v.end(), [&str](const auto& p) {
        return str == p.first;
    });
}

// ----------------------------------------------------------------
//  Manager actions
// ----------------------------------------------------------------

void On_manager_deploy(const ContractID& unused)
{
    Idios::Params params;
    PubKey pk;

    // Derive middleware public key from this wallet
    struct MiddlewareKeyID {
        uint8_t m_Ctx = 1;
    } kid;
    Env::DerivePk(params.middleware_pk, &kid, sizeof(kid));

    Env::GenerateKernel(nullptr, 0, &params, sizeof(params),
        nullptr, 0, nullptr, 0, "Deploy Idios contract", 0);
}

void On_manager_view(const ContractID& unused)
{
    On_error("view not implemented");
}

// ----------------------------------------------------------------
//  User actions — job lifecycle
// ----------------------------------------------------------------

void On_user_create(const ContractID& cid)
{
    Idios::Create args;
    Env::Memset(&args, 0, sizeof(args));

    if (!Env::DocGetNum64("job_id",      &args.job_id))      return On_error("job_id required");
    if (!Env::DocGetNum64("subnet_id",   &args.subnet_id))   return On_error("subnet_id required");
    if (!Env::DocGetNum64("epoch",       &args.epoch))       return On_error("epoch required");
    if (!Env::DocGetNum64("expiry_block",&args.expiry_block)) return On_error("expiry_block required");
    if (!Env::DocGetNum64("payment",     &args.payment))     return On_error("payment required");
    if (!Env::DocGetNum32("asset_id",    &args.asset_id))    return On_error("asset_id required");
    if (!Env::DocGetBlob("node_pk",      &args.node_pk,  sizeof(PubKey))) return On_error("node_pk required");
    if (!Env::DocGetBlob("result_hash",  args.result_hash, 32))           return On_error("result_hash required");

    // Derive requester public key from this wallet
    struct RequesterKeyID {
        ContractID m_Cid;
        uint8_t    m_Ctx = 0;
    } kid;
    kid.m_Cid = cid;
    Env::DerivePk(args.requester_pk, &kid, sizeof(kid));

    // SigRequest — requester must sign
    Env::KeyID sigKid(&kid, sizeof(kid));

    // FundsChange — payment moves from wallet into contract
    FundsChange fc;
    fc.m_Amount  = args.payment;
    fc.m_Aid     = args.asset_id;
    fc.m_Consume = 1; // consume = lock into contract

    Env::GenerateKernel(&cid, Idios::Methods::Action_Create,
        &args, sizeof(args),
        &fc, 1,
        &sigKid, 1,
        "Idios: create job", 0);
}

void On_user_commit(const ContractID& cid)
{
    Idios::Commit args;
    Env::Memset(&args, 0, sizeof(args));

    if (!Env::DocGetNum64("job_id",     &args.job_id))    return On_error("job_id required");
    if (!Env::DocGetNum64("collateral", &args.collateral)) return On_error("collateral required");
    if (!Env::DocGetNum32("asset_id",   &args.asset_id))  return On_error("asset_id required");

    // Derive node public key from this wallet
    struct NodeKeyID {
        ContractID m_Cid;
        uint8_t    m_Ctx = 2;
    } kid;
    kid.m_Cid = cid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    // FundsChange — collateral moves from wallet into contract
    FundsChange fc;
    fc.m_Amount  = args.collateral;
    fc.m_Aid     = args.asset_id;
    fc.m_Consume = 1;

    Env::GenerateKernel(&cid, Idios::Methods::Action_Commit,
        &args, sizeof(args),
        &fc, 1,
        &sigKid, 1,
        "Idios: commit to job", 0);
}

void On_user_refund(const ContractID& cid)
{
    Idios::Refund args;
    Env::Memset(&args, 0, sizeof(args));

    if (!Env::DocGetNum64("job_id", &args.job_id)) return On_error("job_id required");

    // Derive requester public key
    struct RequesterKeyID {
        ContractID m_Cid;
        uint8_t    m_Ctx = 0;
    } kid;
    kid.m_Cid = cid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    Env::GenerateKernel(&cid, Idios::Methods::Action_Refund,
        &args, sizeof(args),
        nullptr, 0,
        &sigKid, 1,
        "Idios: refund job", 0);
}

// ----------------------------------------------------------------
//  Middleware actions — settlement
// ----------------------------------------------------------------

void On_middleware_settle(const ContractID& cid)
{
    Idios::Settle args;
    Env::Memset(&args, 0, sizeof(args));

    if (!Env::DocGetNum64("job_id", &args.job_id)) return On_error("job_id required");
    if (!Env::DocGetBlob("result_hash", args.result_hash, 32)) return On_error("result_hash required");
    Env::DocGetNum64("attestation_pct", &args.attestation_pct); // optional

    // Derive middleware key
    struct MiddlewareKeyID {
        uint8_t m_Ctx = 1;
    } kid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    Env::GenerateKernel(&cid, Idios::Methods::Action_Settle,
        &args, sizeof(args),
        nullptr, 0,
        &sigKid, 1,
        "Idios: settle job", 0);
}

void On_middleware_slash(const ContractID& cid)
{
    Idios::Slash args;
    Env::Memset(&args, 0, sizeof(args));

    if (!Env::DocGetNum64("job_id", &args.job_id)) return On_error("job_id required");

    struct MiddlewareKeyID {
        uint8_t m_Ctx = 1;
    } kid;
    Env::KeyID sigKid(&kid, sizeof(kid));

    Env::GenerateKernel(&cid, Idios::Methods::Action_Slash,
        &args, sizeof(args),
        nullptr, 0,
        &sigKid, 1,
        "Idios: slash job", 0);
}

// ----------------------------------------------------------------
//  View actions — read contract state
// ----------------------------------------------------------------

void On_user_view_job(const ContractID& cid)
{
    uint64_t job_id = 0;
    if (!Env::DocGetNum64("job_id", &job_id)) return On_error("job_id required");

    struct KeyJob {
        uint8_t  prefix = 'J';
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
    Env::DocAddNum64("job_id",       job.job_id);
    Env::DocAddNum64("subnet_id",    job.subnet_id);
    Env::DocAddNum64("payment",      job.payment);
    Env::DocAddNum64("collateral",   job.collateral);
    Env::DocAddNum32("asset_id",     job.asset_id);
    Env::DocAddNum32("status",       (uint32_t)job.status);
    Env::DocAddNum64("expiry_block", job.expiry_block);
    Env::DocAddBlob("node_pk",       &job.node_pk,      sizeof(PubKey));
    Env::DocAddBlob("requester_pk",  &job.requester_pk, sizeof(PubKey));
    Env::DocAddBlob("result_hash",   job.result_hash,   32);
}

// ----------------------------------------------------------------
//  Method_0 — schema export
// ----------------------------------------------------------------

BEAM_EXPORT void Method_0()
{
    Env::DocGroup root("");
    {
        Env::DocGroup gr("roles");
        {
            Env::DocGroup grRole("manager");
            {
                Env::DocGroup grMethod("deploy");
            }
            {
                Env::DocGroup grMethod("view");
            }
        }
        {
            Env::DocGroup grRole("user");
            {
                Env::DocGroup grMethod("create");
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
                Env::DocGroup grMethod("commit");
                Env::DocAddText("job_id",     "uint64");
                Env::DocAddText("collateral", "Amount");
                Env::DocAddText("asset_id",   "AssetID");
            }
            {
                Env::DocGroup grMethod("refund");
                Env::DocAddText("job_id", "uint64");
            }
            {
                Env::DocGroup grMethod("view_job");
                Env::DocAddText("job_id", "uint64");
            }
        }
        {
            Env::DocGroup grRole("middleware");
            {
                Env::DocGroup grMethod("settle");
                Env::DocAddText("job_id",          "uint64");
                Env::DocAddText("result_hash",      "blob32");
                Env::DocAddText("attestation_pct",  "uint64");
            }
            {
                Env::DocGroup grMethod("slash");
                Env::DocAddText("job_id", "uint64");
            }
        }
    }
}

// ----------------------------------------------------------------
//  Method_1 — dispatch
// ----------------------------------------------------------------

BEAM_EXPORT void Method_1()
{
    const Actions_map_t MANAGER_ACTIONS = {
        {"deploy", On_manager_deploy},
        {"view",   On_manager_view},
    };
    const Actions_map_t USER_ACTIONS = {
        {"create",   On_user_create},
        {"commit",   On_user_commit},
        {"refund",   On_user_refund},
        {"view_job", On_user_view_job},
    };
    const Actions_map_t MIDDLEWARE_ACTIONS = {
        {"settle", On_middleware_settle},
        {"slash",  On_middleware_slash},
    };
    const Roles_map_t VALID_ROLES = {
        {"manager",    MANAGER_ACTIONS},
        {"user",       USER_ACTIONS},
        {"middleware", MIDDLEWARE_ACTIONS},
    };

    char action[ACTION_BUF_SIZE], role[ROLE_BUF_SIZE];

    if (!Env::DocGetText("role", role, sizeof(role)))
        return On_error("role required");

    auto it_role = find_if_contains(role, VALID_ROLES);
    if (it_role == VALID_ROLES.end())
        return On_error("invalid role");

    if (!Env::DocGetText("action", action, sizeof(action)))
        return On_error("action required");

    auto it_action = find_if_contains(action, it_role->second);
    if (it_action == it_role->second.end())
        return On_error("invalid action");

    ContractID cid;
    Env::DocGet("cid", cid);
    it_action->second(cid);
}

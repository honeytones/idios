#pragma once

namespace Idios {

#pragma pack (push, 1)

enum JobStatus : uint8_t {
    Open     = 0,
    Active   = 1,
    Settled  = 2,
    Slashed  = 3,
    Refunded = 4,
};

struct Job {
    PubKey   requester_pk;
    PubKey   node_pk;
    Amount   payment;
    Amount   collateral;
    AssetID  asset_id;
    uint64_t job_id;
    uint64_t subnet_id;
    uint64_t epoch;
    uint64_t expiry_block;
    uint8_t  result_hash[32];
    JobStatus status;
};

enum Methods : uint32_t {
    Action_Create  = 2,
    Action_Commit  = 3,
    Action_Settle  = 4,
    Action_Slash   = 5,
    Action_Refund  = 6,
    Action_View    = 7,
};

struct Create {
    uint64_t job_id;
    uint64_t subnet_id;
    uint64_t epoch;
    uint64_t expiry_block;
    PubKey   node_pk;
    PubKey   requester_pk;
    Amount   payment;
    AssetID  asset_id;
    uint8_t  result_hash[32];
};

struct Commit {
    uint64_t job_id;
    Amount   collateral;
    AssetID  asset_id;
};

struct Settle {
    uint64_t job_id;
    uint8_t  result_hash[32];
    uint64_t attestation_pct;
};

struct Slash {
    uint64_t job_id;
};

struct Refund {
    uint64_t job_id;
};

struct View {
    uint64_t job_id;
};

struct Params {
    PubKey middleware_pk;
};

#pragma pack (pop)

} // namespace Idios

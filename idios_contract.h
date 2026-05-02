#pragma once

namespace Idios {

    static const ShaderID s_SID = {0xfb,0x21,0xd4,0x6b,0x65,0xf3,0x8b,0xb5,0xac,0xe8,0xde,0x34,0x28,0xfb,0x99,0x7f,0x6e,0xf4,0x60,0xd3,0x01,0xf2,0xfa,0xc0,0x5d,0x6d,0x14,0xcd,0x35,0xea,0xa5,0x3f};

#pragma pack (push, 1)

struct Tags {
    static const uint8_t s_Job    = 0;
    static const uint8_t s_Params = 1;
};

enum JobStatus : uint8_t {
    Open              = 0,
    Active            = 1,
    AwaitingApproval  = 2,
    Disputed          = 3,
    Settled           = 4,
    Refunded          = 5,
    ResolvedToAlice   = 6,
    ResolvedToBob     = 7,
    Closed            = 8,
};

enum JobMode : uint8_t {
    ModeA = 'A',
    ModeB = 'B',
};

struct Job {
    PubKey    requester_pk;
    PubKey    node_pk;
    Amount    payment;
    Amount    collateral;
    Amount    dispute_fee;
    AssetID   asset_id;
    uint64_t  job_id;
    uint64_t  subnet_id;
    uint64_t  epoch;
    Height    expiry_block;
    Height    review_window_blocks;
    Height    review_deadline_block;
    Height    dispute_filed_block;
    uint8_t   result_hash[32];
    uint8_t   delivery_hash[32];
    uint8_t   mode;
    JobStatus status;
};

struct Params {
    PubKey arbitrator_pk;
    Height default_review_window;
    Height arbitrator_timeout_blocks;
};

struct CreateModeA {
    static const uint32_t s_iMethod = 2;
    uint64_t  job_id;
    uint64_t  subnet_id;
    uint64_t  epoch;
    Height    expiry_block;
    PubKey    node_pk;
    PubKey    requester_pk;
    Amount    payment;
    AssetID   asset_id;
    uint8_t   result_hash[32];
};

struct Commit {
    static const uint32_t s_iMethod = 3;
    uint64_t  job_id;
    Amount    collateral;
    AssetID   asset_id;
};

struct Refund {
    static const uint32_t s_iMethod = 6;
    uint64_t  job_id;
};

struct CreateModeB {
    static const uint32_t s_iMethod = 8;
    uint64_t  job_id;
    uint64_t  subnet_id;
    uint64_t  epoch;
    Height    expiry_block;
    Height    review_window_blocks;
    PubKey    node_pk;
    PubKey    requester_pk;
    Amount    payment;
    Amount    dispute_fee;
    AssetID   asset_id;
};

struct SubmitDelivery {
    static const uint32_t s_iMethod = 9;
    uint64_t  job_id;
    uint8_t   delivery_hash[32];
};

struct Approve {
    static const uint32_t s_iMethod = 10;
    uint64_t  job_id;
};

struct Dispute {
    static const uint32_t s_iMethod = 11;
    uint64_t  job_id;
};

struct ResolveToAlice {
    static const uint32_t s_iMethod = 12;
    uint64_t  job_id;
};

struct ResolveToBob {
    static const uint32_t s_iMethod = 13;
    uint64_t  job_id;
};

struct ClaimAfterTimeout {
    static const uint32_t s_iMethod = 14;
    uint64_t  job_id;
};
struct Claim {
    static const uint32_t s_iMethod = 15;
    uint64_t  job_id;
};

struct View {
    static const uint32_t s_iMethod = 7;
    uint64_t  job_id;
};

#pragma pack (pop)

} // namespace Idios

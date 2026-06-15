#pragma once

#include "Shaders/upgradable3/contract.h"

namespace Idios {

    static const ShaderID s_SID = {0x16,0xda,0xcc,0xe9,0x67,0x44,0x94,0xa3,0x06,0x1b,0xc7,0xf4,0x10,0x44,0x49,0xaa,0x6a,0x7e,0x98,0x10,0xe3,0x92,0xba,0x19,0xc9,0x23,0xc2,0x6e,0x08,0xa8,0xe0,0xb7};

#pragma pack (push, 1)

struct Tags {
    static const uint8_t s_Job    = 0;
    static const uint8_t s_Params = 1;
};

// Key structs live inside the packed region so the contract and the app
// shader serialize identical, deterministic key bytes (9 bytes, no padding).
// This is the v5 fix for the KeyJob padding issue: the old out-of-header
// definitions had 7 indeterminate padding bytes between prefix and job_id.
struct KeyJob {
    uint8_t  prefix = Tags::s_Job;
    uint64_t job_id;
};

struct KeyParams {
    uint8_t prefix = Tags::s_Params;
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
    Voided            = 9,   // dispute abandoned by arbitrator (timed out)
    Cancelled         = 10,  // mutual cancel: both signed, everyone made whole
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
    Amount    required_collateral; // v5: floor for Commit; 0 = no floor
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
    uint8_t   spec_hash[32];         // v5: hash of the agreed work spec, stored only
    uint8_t   mode;
    JobStatus status;
};

struct Params {
    PubKey arbitrator_pk;
    PubKey treasury_pk;
    Height default_review_window;
    Height arbitrator_timeout_blocks;
};

// Ctor argument (Upgradable3). m_Upgradable MUST come first, matching the
// standard amm/nephrite layout: the Ctor reads the upgradable admin settings
// before the contract's own params. m_Params is the previous Ctor argument
// unchanged.
struct Create {
    Upgradable3::Settings m_Upgradable;
    Params                m_Params;
};

struct CreateModeA {
    static const uint32_t s_iMethod = 4; // was 2; moved to free Method 2 for Upgradable3 control
    uint64_t  job_id;
    uint64_t  subnet_id;
    uint64_t  epoch;
    Height    expiry_block;
    PubKey    node_pk;
    PubKey    requester_pk;
    Amount    payment;
    Amount    required_collateral; // v5: 0 = no floor
    AssetID   asset_id;
    uint8_t   result_hash[32];
    uint8_t   spec_hash[32];       // v5: optional, may be zero
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
    Height    review_window_blocks; // v5: 0 means use params.default_review_window
    PubKey    node_pk;
    PubKey    requester_pk;
    Amount    payment;
    Amount    dispute_fee;
    Amount    required_collateral;  // v5: 0 = no floor
    AssetID   asset_id;
    uint8_t   spec_hash[32];        // v5: optional, may be zero
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

struct VoidStaleDispute {
    static const uint32_t s_iMethod = 16;
    uint64_t  job_id;
};

struct VoidClaimRequester {
    static const uint32_t s_iMethod = 17;
    uint64_t  job_id;
};

struct VoidClaimNode {
    static const uint32_t s_iMethod = 18;
    uint64_t  job_id;
};

struct TreasurySweep {
    static const uint32_t s_iMethod = 19;
    uint64_t  job_id;
};

struct MutualCancel {
    static const uint32_t s_iMethod = 20;
    uint64_t  job_id;
};

struct View {
    static const uint32_t s_iMethod = 7;
    uint64_t  job_id;
};

#pragma pack (pop)

} // namespace Idios

#pragma once

#include "Shaders/upgradable3/contract.h"

namespace Idios {

    static const ShaderID s_SID = {0xa6,0x1f,0x3a,0x93,0xf5,0x5b,0x9e,0xab,0xcc,0xd9,0x83,0x75,0x77,0xf1,0xf7,0x2a,0x0d,0x77,0x5b,0x43,0xd2,0xdf,0x6e,0xe3,0x0b,0x15,0x48,0xe6,0xee,0x52,0x3b,0x3c};

#pragma pack (push, 1)

struct Tags {
    static const uint8_t s_Job      = 0;
    static const uint8_t s_Params   = 1;
    // M of N (v1) additions. New namespaces only; the two above are unchanged
    // so existing Job and Params storage is read back identically.
    static const uint8_t s_Dispute  = 2; // per disputed job: frozen N/M, tallies, resolution
    static const uint8_t s_Arb      = 3; // per arbitrator: bond and registry state
    static const uint8_t s_Vote      = 4; // per (job, arbitrator): the cast vote
    static const uint8_t s_RegCount = 5; // single counter: live registered bonds (N)
    // v2 addition (worker reputation bond). New namespace only, everything
    // above is unchanged.
    static const uint8_t s_WorkerBond = 6; // per worker (node pk): slashable bond
};

// Key structs live inside the packed region so the contract and the app
// shader serialize identical, deterministic key bytes (no padding).
struct KeyJob {
    uint8_t  prefix = Tags::s_Job;
    uint64_t job_id;
};

struct KeyParams {
    uint8_t prefix = Tags::s_Params;
};

struct KeyDispute {
    uint8_t  prefix = Tags::s_Dispute;
    uint64_t job_id;
};

struct KeyArb {
    uint8_t prefix = Tags::s_Arb;
    PubKey  arb_pk;
};

struct KeyVote {
    uint8_t  prefix = Tags::s_Vote;
    uint64_t job_id;
    PubKey   arb_pk;
};

struct KeyRegCount {
    uint8_t prefix = Tags::s_RegCount;
};

struct KeyWorkerBond {
    uint8_t prefix = Tags::s_WorkerBond;
    PubKey  worker_pk;
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
    Voided            = 9,   // dispute abandoned (timed out, or never reached quorum)
    Cancelled         = 10,  // mutual cancel: both signed, everyone made whole
};

enum JobMode : uint8_t {
    ModeA = 'A',
    ModeB = 'B',
};

// UNCHANGED from v6. Byte identical layout so existing jobs survive the
// in place upgrade. All M of N per job state lives in DisputeState, not here.
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
    uint8_t   spec_hash[32];
    uint8_t   mode;
    JobStatus status;
};

// UNCHANGED from v6. Byte identical so the stored params survive the upgrade.
// Note: arbitrator_pk is the v0 single arbitrator key, retained only so that
// in flight v0 disputes are not orphaned by the struct; v1 resolves by quorum
// and does not read it. arbitrator_timeout_blocks doubles as the bond reclaim
// cooldown in v1 (no new param, no Ctor change).
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

// ---- M of N (v1) records -------------------------------------------------

// resolution: 0 = none, 1 = Alice, 2 = Bob. Created at Dispute, finalised at
// the Mth matching vote. The whole dispute_fee leaves as threshold shares of
// fee_share plus the single fee_remainder swept to treasury.
// v2: bond_encumbered appended (field offsets above it unchanged). Safe to
// grow this struct in the v1 -> v2 upgrade ONLY because production has zero
// dispute records (explorer verified, July 2026); a chain with live v1
// disputes could not extend it, LoadDispute size checks would orphan them.
struct DisputeState {
    uint64_t frozen_n;       // live registry size N, frozen at dispute time
    uint32_t threshold;      // M = N/2 + 1 (1 if N == 0)
    uint32_t vc_alice;       // running tally, side Alice
    uint32_t vc_bob;         // running tally, side Bob
    Amount   fee_share;      // dispute_fee / M, set at resolution
    Amount   fee_remainder;  // dispute_fee % M, swept to treasury
    uint8_t  resolution;     // 0 none, 1 Alice, 2 Bob
    uint8_t  winner_paid;    // P + C claimed by the winner
    uint8_t  remainder_swept;// fee_remainder taken by treasury
    uint8_t  bond_encumbered;// v2: this dispute holds an encumbrance on the
                             // worker's bond (set at filing, cleared once at
                             // resolution or void)
};

// state: 0 = registered, 1 = deregistering, 2 = gone. The bond is pure sybil
// resistance, never slashed; reclaimed in full after the cooldown.
// v2: registration is hardened (BEAM only, s_MinArbBond floor, admin co
// sign) and a gone record may be overwritten by a fresh registration.
struct ArbRec {
    Amount  stake;
    AssetID asset_id;
    Height  registered_at;   // only arbs registered before a dispute may vote on it
    Height  dereg_block;
    uint8_t state;
};

// v2 arbitrator bond floor: 10 BEAM in groth. Hardcoded, not a Ctor param,
// so Params stays byte identical across the upgrade.
static const Amount s_MinArbBond = 1000000000ULL;

// v2 worker reputation bond, keyed by the worker's node pk (Tags
// s_WorkerBond). BEAM only (no asset field; the register gate enforces
// asset 0), no floor: the off chain score reader shows the amount, dust is
// self defeating. state: 0 registered, 1 deregistering, 2 gone, 3 slashed.
// encumbrances counts live disputes that froze this bond at filing; reclaim
// halts while it is nonzero (closes the mid dispute escape) and slash_sweep
// waits for it to reach zero (an early sweep would free the identity to re
// bond while an old dispute could still hit the fresh bond).
struct WorkerBondRec {
    Amount   stake;
    Height   bonded_at;
    Height   dereg_block;
    uint32_t encumbrances;
    uint8_t  state;
};

// side: 0 = Alice, 1 = Bob. One immutable record per (job, arbitrator).
struct VoteRec {
    uint8_t side;
    uint8_t claimed;         // reward share taken (consensus voters only)
};

struct RegCount {
    uint64_t n;
};

// ---- method argument structs ---------------------------------------------

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

// RETIRED in v1: single arbitrator resolve. Kept as a struct for ABI/method
// numbering only; Method_12 and Method_13 are Halt stubs now. Disputes resolve
// by quorum (Vote, Method_24).
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

// ---- M of N (v1) methods --------------------------------------------------

struct Register {
    static const uint32_t s_iMethod = 21;
    PubKey   arb_pk;
    Amount   stake;
    AssetID  asset_id;
};

struct Deregister {
    static const uint32_t s_iMethod = 22;
    PubKey   arb_pk;
};

struct ReclaimStake {
    static const uint32_t s_iMethod = 23;
    PubKey   arb_pk;
};

struct Vote {
    static const uint32_t s_iMethod = 24;
    uint64_t job_id;
    PubKey   arb_pk;
    uint8_t  side;          // 0 = Alice, 1 = Bob
};

struct ClaimArbReward {
    static const uint32_t s_iMethod = 25;
    uint64_t job_id;
    PubKey   arb_pk;
};

// ---- v2 methods (worker reputation bond) -----------------------------------

struct WorkerRegister {
    static const uint32_t s_iMethod = 26;
    PubKey   worker_pk;
    Amount   stake;
    AssetID  asset_id;      // must be 0 (BEAM); kept explicit so the gate is visible
};

struct WorkerDeregister {
    static const uint32_t s_iMethod = 27;
    PubKey   worker_pk;
};

struct WorkerReclaim {
    static const uint32_t s_iMethod = 28;
    PubKey   worker_pk;
};

// Treasury pulls a slashed bond. Waits for encumbrances == 0 (see
// WorkerBondRec comment).
struct SlashSweep {
    static const uint32_t s_iMethod = 29;
    PubKey   worker_pk;
};

struct View {
    static const uint32_t s_iMethod = 7;
    uint64_t  job_id;
};

#pragma pack (pop)

} // namespace Idios

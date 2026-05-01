#include "Shaders/common.h"
#include "Shaders/Math.h"
#include "idios_contract.h"

struct KeyJob {
    uint8_t  prefix = Idios::Tags::s_Job;
    uint64_t job_id;
};

struct KeyParams {
    uint8_t prefix = Idios::Tags::s_Params;
};

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

BEAM_EXPORT void Ctor(const Idios::Params& params) {
    Env::Halt_if(Env::Memis0(&params.arbitrator_pk, sizeof(params.arbitrator_pk)));
    Env::Halt_if(params.default_review_window == 0);
    Env::Halt_if(params.arbitrator_timeout_blocks == 0);
    KeyParams key;
    Env::SaveVar(&key, sizeof(key), &params, sizeof(params), KeyTag::Internal);
}

BEAM_EXPORT void Dtor(void*) {}

BEAM_EXPORT void Method_2(const Idios::CreateModeA& args) {
    Env::Halt_if(args.payment == 0);
    Env::Halt_if(Env::Memis0(&args.node_pk, sizeof(args.node_pk)));
    Env::Halt_if(Env::Memis0(&args.requester_pk, sizeof(args.requester_pk)));
    Env::Halt_if(Env::Memis0(args.result_hash, 32));
    Env::Halt_if(args.expiry_block <= Env::get_Height());
    Env::Halt_if(JobIdInUse(args.job_id));

    Idios::Job job;
    Env::Memset(&job, 0, sizeof(job));
    job.job_id       = args.job_id;
    job.subnet_id    = args.subnet_id;
    job.epoch        = args.epoch;
    job.expiry_block = args.expiry_block;
    job.payment      = args.payment;
    job.collateral   = 0;
    job.asset_id     = args.asset_id;
    job.mode         = Idios::JobMode::ModeA;
    job.status       = Idios::JobStatus::Open;
    Env::Memcpy(&job.node_pk,      &args.node_pk,      sizeof(PubKey));
    Env::Memcpy(&job.requester_pk, &args.requester_pk, sizeof(PubKey));
    Env::Memcpy(job.result_hash,   args.result_hash,   32);

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
    Env::Halt_if(args.asset_id != job.asset_id);

    Env::AddSig(job.node_pk);
    Env::FundsLock(args.asset_id, args.collateral);
    job.collateral = args.collateral;
    job.status     = Idios::JobStatus::Active;
    SaveJob(job);
}

BEAM_EXPORT void Method_4(void*) { Env::Halt(); }
BEAM_EXPORT void Method_5(void*) { Env::Halt(); }

BEAM_EXPORT void Method_6(const Idios::Refund& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));
    Env::Halt_if(job.status == Idios::JobStatus::Settled);
    Env::Halt_if(job.status == Idios::JobStatus::Refunded);
    Env::Halt_if(job.status == Idios::JobStatus::Disputed);
    Env::Halt_if(job.status == Idios::JobStatus::AwaitingApproval);
    Env::Halt_if(Env::get_Height() <= job.expiry_block);

    Env::AddSig(job.requester_pk);
    Env::FundsUnlock(job.asset_id, job.payment + job.collateral);
    job.status = Idios::JobStatus::Refunded;
    SaveJob(job);
}

BEAM_EXPORT void Method_7(void*) { Env::Halt(); }

BEAM_EXPORT void Method_8(const Idios::CreateModeB& args) {
    Env::Halt_if(args.payment == 0);
    Env::Halt_if(args.dispute_fee == 0);
    Env::Halt_if(args.review_window_blocks == 0);
    Env::Halt_if(Env::Memis0(&args.node_pk, sizeof(args.node_pk)));
    Env::Halt_if(Env::Memis0(&args.requester_pk, sizeof(args.requester_pk)));
    Env::Halt_if(args.expiry_block <= Env::get_Height());
    Env::Halt_if(JobIdInUse(args.job_id));

    Idios::Job job;
    Env::Memset(&job, 0, sizeof(job));
    job.job_id              = args.job_id;
    job.subnet_id           = args.subnet_id;
    job.epoch               = args.epoch;
    job.expiry_block        = args.expiry_block;
    job.review_window_blocks = args.review_window_blocks;
    job.payment             = args.payment;
    job.collateral          = 0;
    job.dispute_fee         = args.dispute_fee;
    job.asset_id            = args.asset_id;
    job.mode                = Idios::JobMode::ModeB;
    job.status              = Idios::JobStatus::Open;
    Env::Memcpy(&job.node_pk,      &args.node_pk,      sizeof(PubKey));
    Env::Memcpy(&job.requester_pk, &args.requester_pk, sizeof(PubKey));

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
        job.status = Idios::JobStatus::Settled;
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
    Env::FundsUnlock(job.asset_id, job.payment + job.collateral);
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
    Env::FundsUnlock(job.asset_id, job.payment + job.collateral + job.dispute_fee);
    job.status = Idios::JobStatus::Settled;
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
    Env::FundsUnlock(job.asset_id, job.payment + job.collateral + job.dispute_fee);
    job.status = Idios::JobStatus::Settled;
    SaveJob(job);
}

BEAM_EXPORT void Method_14(const Idios::ClaimAfterTimeout& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));
    Env::Halt_if(job.mode != Idios::JobMode::ModeB);
    Env::Halt_if(job.status != Idios::JobStatus::AwaitingApproval);
    Env::Halt_if(Env::get_Height() <= job.review_deadline_block);

    Env::AddSig(job.node_pk);
    Env::FundsUnlock(job.asset_id, job.payment + job.collateral);
    job.status = Idios::JobStatus::Settled;
    SaveJob(job);
}

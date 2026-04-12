#include "Shaders/common.h"
#include "Shaders/Math.h"
#include "idios_contract.h"

struct KeyJob {
    uint8_t  prefix = 'J';
    uint64_t job_id;
};

struct KeyParams {
    uint8_t prefix = 'P';
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
}

static bool LoadParams(Idios::Params& params) {
    KeyParams key;
    uint32_t n = Env::LoadVar(&key, sizeof(key), &params, sizeof(params), KeyTag::Internal);
    return n == sizeof(params);
}

static bool HashesMatch(const uint8_t* a, const uint8_t* b) {
    return Env::Memcmp(a, b, 32) == 0;
}

BEAM_EXPORT void Ctor(const Idios::Params& params) {
    Env::Halt_if(Env::Memis0(&params.middleware_pk, sizeof(params.middleware_pk)));
    KeyParams key;
    Env::SaveVar(&key, sizeof(key), &params, sizeof(params), KeyTag::Internal);
}

BEAM_EXPORT void Dtor(void*) {}

BEAM_EXPORT void Method_2(const Idios::Create& args) {
    Env::Halt_if(args.payment == 0);
    Env::Halt_if(Env::Memis0(&args.node_pk, sizeof(args.node_pk)));
    Env::Halt_if(Env::Memis0(&args.requester_pk, sizeof(args.requester_pk)));
    Env::Halt_if(Env::Memis0(args.result_hash, 32));
    Env::Halt_if(args.expiry_block <= Env::get_Height());

    Idios::Job existing;
    KeyJob key;
    key.job_id = args.job_id;
    uint32_t existing_size = Env::LoadVar(&key, sizeof(key), &existing, sizeof(existing), KeyTag::Internal);
    Env::Halt_if(existing_size > 0);

    Idios::Job job;
    Env::Memset(&job, 0, sizeof(job));

    job.job_id       = args.job_id;
    job.subnet_id    = args.subnet_id;
    job.epoch        = args.epoch;
    job.expiry_block = args.expiry_block;
    job.payment      = args.payment;
    job.collateral   = 0;
    job.asset_id     = args.asset_id;
    job.status       = Idios::JobStatus::Open;

    Env::Memcpy(&job.node_pk,      &args.node_pk,      sizeof(PubKey));
    Env::Memcpy(&job.requester_pk, &args.requester_pk, sizeof(PubKey));
    Env::Memcpy(job.result_hash,   args.result_hash,   32);

    Env::AddSig(args.requester_pk);
    Env::FundsLock(args.asset_id, args.payment);
    Env::EmitLog(&key, sizeof(key), &job, sizeof(job), KeyTag::Internal);

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

BEAM_EXPORT void Method_4(const Idios::Settle& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));
    Env::Halt_if(job.status != Idios::JobStatus::Active);
    Env::Halt_if(!HashesMatch(args.result_hash, job.result_hash));

    Idios::Params params;
    Env::Halt_if(!LoadParams(params));
    Env::AddSig(params.middleware_pk);

    Env::FundsUnlock(job.asset_id, job.payment);
    if (job.collateral > 0)
        Env::FundsUnlock(job.asset_id, job.collateral);

    job.status = Idios::JobStatus::Settled;
    SaveJob(job);
}

BEAM_EXPORT void Method_5(const Idios::Slash& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));
    Env::Halt_if(job.status != Idios::JobStatus::Active);

    Idios::Params params;
    Env::Halt_if(!LoadParams(params));
    Env::AddSig(params.middleware_pk);

    Env::FundsUnlock(job.asset_id, job.payment);
    if (job.collateral > 0)
        Env::FundsUnlock(job.asset_id, job.collateral);

    job.status = Idios::JobStatus::Slashed;
    SaveJob(job);
}

BEAM_EXPORT void Method_6(const Idios::Refund& args) {
    Idios::Job job;
    Env::Halt_if(!LoadJob(args.job_id, job));
    Env::Halt_if(job.status == Idios::JobStatus::Settled);
    Env::Halt_if(job.status == Idios::JobStatus::Slashed);
    Env::Halt_if(job.status == Idios::JobStatus::Refunded);
    Env::Halt_if(Env::get_Height() <= job.expiry_block);

    Env::AddSig(job.requester_pk);
    Env::FundsUnlock(job.asset_id, job.payment);

    if (job.status == Idios::JobStatus::Active && job.collateral > 0)
        Env::FundsUnlock(job.asset_id, job.collateral);

    job.status = Idios::JobStatus::Refunded;
    SaveJob(job);
}

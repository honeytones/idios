import Utils from '@core/utils.js';

const CID = 'e595078e08f00f471e7781b8e64f1d1303fa61b838f881dd646ec5f701d9251d';
const SHADER_URL = './contract.wasm';

let shader = null;

export async function loadShader() {
    if (shader) return shader;
    return new Promise((resolve, reject) => {
        Utils.download(SHADER_URL, (err, bytes) => {
            if (err) return reject(err);
            shader = bytes;
            resolve(shader);
        });
    });
}

export async function createJob(job_id, node_pk, result_hash, payment, expiry_block, subnet_id = 1) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=create,cid=${CID},job_id=${job_id},subnet_id=${subnet_id},epoch=1,expiry_block=${expiry_block},payment=${payment},asset_id=0,node_pk=${node_pk},result_hash=${result_hash}`;
        Utils.invokeContract(args, (err, result, full) => {
            console.log('invokeContract response - err:', JSON.stringify(err), 'result:', JSON.stringify(result), 'full:', JSON.stringify(full));
            if (err) return reject(err);
            onMakeTx(err, result, full);
            resolve(result);
        }, shader);
    });
}

export async function commitJob(job_id, collateral) {
    return new Promise((resolve, reject) => {
        const args = `role=user,action=commit,cid=${CID},job_id=${job_id},collateral=${collateral},asset_id=0`;
        Utils.invokeContract(args, (err, result, full) => {
            if (err) return reject(err);
            onMakeTx(err, result, full);
            resolve(result);
        }, null);
    });
}

export async function viewJob(job_id) {
    return new Promise((resolve, reject) => {
        const args = `role=user,action=view_job,cid=${CID},job_id=${job_id}`;
        Utils.invokeContract(args, (err, result, full) => {
            if (err) return reject(err);
            resolve(result);
        }, null);
    });
}

export async function refundJob(job_id) {
    return new Promise((resolve, reject) => {
        const args = `role=user,action=refund,cid=${CID},job_id=${job_id}`;
        Utils.invokeContract(args, (err, result, full) => {
            if (err) return reject(err);
            onMakeTx(err, result, full);
            resolve(result);
        }, null);
    });
}

const onMakeTx = (err, result, full) => {
    if (err) {
        console.log(err, 'Failed to generate transaction request');
        return;
    }
    Utils.callApi('process_invoke_data', { data: full.result.raw_data }, (error, result, full) => {
        if (error) console.log('process_invoke_data error:', error);
    });
};

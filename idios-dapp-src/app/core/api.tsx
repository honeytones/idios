import Utils from '@core/utils.js';

const CID = '74c497b7fe906c09e0da91d1a5e43b2afe122b1a6af3ae74c9440259d6f27027';
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
        }, shader);
    });
}

export async function viewJob(job_id) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=view_job,cid=${CID},job_id=${job_id}`;
        Utils.invokeContract(args, (err, result, full) => {
            try {
                const raw = full && full.result && full.result.output;
                if (raw) {
                    const parsed = JSON.parse('{' + raw + '}');
                    return resolve(parsed.job || parsed);
                }
            } catch(e) {}
            if (err) return reject(new Error(JSON.stringify(err)));
            resolve(result);
        }, shader);
    });
}

export async function refundJob(job_id, payment, collateral, asset_id = 0) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=refund,cid=${CID},job_id=${job_id},payment=${payment},collateral=${collateral},asset_id=${asset_id}`;
        Utils.invokeContract(args, (err, result, full) => {
            console.log('refundJob response - err:', JSON.stringify(err), 'result:', JSON.stringify(result), 'full:', JSON.stringify(full));
            if (err) return reject(err);
            onMakeTx(err, result, full);
            resolve(result);
        }, shader);
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

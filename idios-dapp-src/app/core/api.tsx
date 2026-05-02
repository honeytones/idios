import Utils from '@core/utils.js';

const CID = 'f40eb64da63a69d91afa1a947d9d272a9f80027d7261aa822ec0e4b5827cdc45';
const SHADER_URL = './amm.wasm';

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

const onMakeTx = (err, result, full) => {
    if (err) {
        console.log(err, 'Failed to generate transaction request');
        return;
    }
    Utils.callApi('process_invoke_data', { data: full.result.raw_data }, (error, result, full) => {
        if (error) console.log('process_invoke_data error:', error);
    });
};

function invokeContract(args, resolve, reject) {
    Utils.invokeContract(args, (err, result, full) => {
        console.log('invokeContract response - err:', JSON.stringify(err), 'result:', JSON.stringify(result), 'full:', JSON.stringify(full));
        if (err) return reject(err);
        onMakeTx(err, result, full);
        resolve(result);
    }, shader);
}

export async function createJobModeA(job_id, node_pk, result_hash, payment, expiry_block, subnet_id = 1) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=create_a,cid=${CID},job_id=${job_id},subnet_id=${subnet_id},epoch=1,expiry_block=${expiry_block},payment=${payment},asset_id=0,node_pk=${node_pk},result_hash=${result_hash}`;
        invokeContract(args, resolve, reject);
    });
}

export async function createJobModeB(job_id, node_pk, payment, dispute_fee, expiry_block, review_window_blocks, subnet_id = 1) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=create_b,cid=${CID},job_id=${job_id},subnet_id=${subnet_id},epoch=1,expiry_block=${expiry_block},review_window_blocks=${review_window_blocks},payment=${payment},dispute_fee=${dispute_fee},asset_id=0,node_pk=${node_pk}`;
        invokeContract(args, resolve, reject);
    });
}

export async function createJob(job_id, node_pk, result_hash, payment, expiry_block, subnet_id = 1) {
    return createJobModeA(job_id, node_pk, result_hash, payment, expiry_block, subnet_id);
}

export async function commitJob(job_id, collateral) {
    return new Promise((resolve, reject) => {
        const args = `role=user,action=commit,cid=${CID},job_id=${job_id},collateral=${collateral},asset_id=0`;
        invokeContract(args, resolve, reject);
    });
}

export async function submitDelivery(job_id, delivery_hash, mode, payment, collateral) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const modeNum = mode === 'A' ? 65 : 66;
        const args = `role=user,action=submit_delivery,cid=${CID},job_id=${job_id},delivery_hash=${delivery_hash},mode=${modeNum},payment=${payment},collateral=${collateral},asset_id=0`;
        invokeContract(args, resolve, reject);
    });
}

export async function approveJob(job_id, payment, collateral) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=approve,cid=${CID},job_id=${job_id},payment=${payment},collateral=${collateral},asset_id=0`;
        invokeContract(args, resolve, reject);
    });
}

export async function disputeJob(job_id, dispute_fee) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=dispute,cid=${CID},job_id=${job_id},dispute_fee=${dispute_fee},asset_id=0`;
        invokeContract(args, resolve, reject);
    });
}

export async function claimAfterTimeout(job_id, payment, collateral) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=claim_after_timeout,cid=${CID},job_id=${job_id},payment=${payment},collateral=${collateral},asset_id=0`;
        invokeContract(args, resolve, reject);
    });
}

export async function refundJob(job_id, payment, collateral, asset_id = 0) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=refund,cid=${CID},job_id=${job_id},payment=${payment},collateral=${collateral},asset_id=${asset_id}`;
        invokeContract(args, resolve, reject);
    });
}

export async function claimJob(job_id, total) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=claim,cid=${CID},job_id=${job_id},total=${total},asset_id=0`;
        invokeContract(args, resolve, reject);
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

export async function resolveToAlice(job_id, total) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=arbitrator,action=resolve_alice,cid=${CID},job_id=${job_id},total=${total},asset_id=0`;
        invokeContract(args, resolve, reject);
    });
}

export async function resolveToBob(job_id, total) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=arbitrator,action=resolve_bob,cid=${CID},job_id=${job_id},total=${total},asset_id=0`;
        invokeContract(args, resolve, reject);
    });
}

import Utils from '@core/utils.js';

const CID = '41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f';
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

export async function createJobModeA(job_id, node_pk, result_hash, payment, expiry_block, subnet_id = 1, asset_id = 0) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=create_a,cid=${CID},job_id=${job_id},subnet_id=${subnet_id},epoch=1,expiry_block=${expiry_block},payment=${payment},asset_id=${asset_id},node_pk=${node_pk},result_hash=${result_hash}`;
        invokeContract(args, resolve, reject);
    });
}

export async function createJobModeB(job_id, node_pk, payment, dispute_fee, expiry_block, review_window_blocks, subnet_id = 1, asset_id = 0) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=create_b,cid=${CID},job_id=${job_id},subnet_id=${subnet_id},epoch=1,expiry_block=${expiry_block},review_window_blocks=${review_window_blocks},payment=${payment},dispute_fee=${dispute_fee},asset_id=${asset_id},node_pk=${node_pk}`;
        invokeContract(args, resolve, reject);
    });
}

export async function createJob(job_id, node_pk, result_hash, payment, expiry_block, subnet_id = 1) {
    return createJobModeA(job_id, node_pk, result_hash, payment, expiry_block, subnet_id);
}

export async function commitJob(job_id, collateral, asset_id = 0) {
    return new Promise((resolve, reject) => {
        const args = `role=user,action=commit,cid=${CID},job_id=${job_id},collateral=${collateral},asset_id=${asset_id}`;
        invokeContract(args, resolve, reject);
    });
}

export async function submitDelivery(job_id, delivery_hash) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=submit_delivery,cid=${CID},job_id=${job_id},delivery_hash=${delivery_hash}`;
        invokeContract(args, resolve, reject);
    });
}

export async function approveJob(job_id) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=approve,cid=${CID},job_id=${job_id}`;
        invokeContract(args, resolve, reject);
    });
}
export async function mutualCancel(job_id) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=mutual_cancel,cid=${CID},job_id=${job_id}`;
        invokeContract(args, resolve, reject);
    });
}

export async function disputeJob(job_id, dispute_fee, asset_id = 0) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=dispute,cid=${CID},job_id=${job_id},dispute_fee=${dispute_fee},asset_id=${asset_id}`;
        invokeContract(args, resolve, reject);
    });
}

export async function claimAfterTimeout(job_id) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=claim_after_timeout,cid=${CID},job_id=${job_id}`;
        invokeContract(args, resolve, reject);
    });
}

export async function refundJob(job_id) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=refund,cid=${CID},job_id=${job_id}`;
        invokeContract(args, resolve, reject);
    });
}

export async function claimJob(job_id) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=claim,cid=${CID},job_id=${job_id}`;
        invokeContract(args, resolve, reject);
    });
}

export async function viewJob(job_id) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=manager,action=view_job,cid=${CID},job_id=${job_id}`;
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

export async function getUserKey() {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=get_key,cid=${CID}`;
        Utils.invokeContract(args, (err, result, full) => {
            try {
                const raw = full && full.result && full.result.output;
                if (raw) {
                    const parsed = JSON.parse('{' + raw + '}');
                    const pk = parsed.key && parsed.key.pub_key;
                    if (pk) return resolve(pk);
                }
            } catch(e) {}
            if (err) return reject(new Error(JSON.stringify(err)));
            resolve(result);
        }, shader);
    });
}
// M of N arbitration (v2). A dispute is resolved when a majority of the
// registry frozen onto it vote the same side. side: 0 = requester (Alice),
// 1 = worker (Bob). arb_index selects which of this wallet's arbitrator
// identities signs; separate wallets all use index 0.
export async function voteDispute(job_id, side, arb_index = 0) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=arbitrator,action=vote,cid=${CID},job_id=${job_id},side=${side},arb_index=${arb_index}`;
        invokeContract(args, resolve, reject);
    });
}

// After resolution, each consensus voter claims their share of the dispute
// fee (fee divided by the threshold M). Fails on chain for a non voter, a
// minority voter, or a second claim.
export async function claimArbReward(job_id, arb_index = 0) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=arbitrator,action=claim_reward,cid=${CID},job_id=${job_id},arb_index=${arb_index}`;
        invokeContract(args, resolve, reject);
    });
}

// Per dispute M of N state. Resolves null when the job has no dispute record.
export async function viewDispute(job_id) {
    await loadShader();
    return new Promise((resolve) => {
        const args = `role=manager,action=view_dispute,cid=${CID},job_id=${job_id}`;
        Utils.invokeContract(args, (err, result, full) => {
            try {
                const raw = full && full.result && full.result.output;
                if (raw) {
                    const parsed = JSON.parse('{' + raw + '}');
                    if (parsed.dispute) return resolve(parsed.dispute);
                }
            } catch(e) {}
            resolve(null);
        }, shader);
    });
}

// This wallet's arbitrator registration at the given index. Resolves null
// when no arbitrator is registered under that key.
export async function viewArb(arb_index = 0) {
    await loadShader();
    return new Promise((resolve) => {
        const args = `role=arbitrator,action=view_arb,cid=${CID},arb_index=${arb_index}`;
        Utils.invokeContract(args, (err, result, full) => {
            try {
                const raw = full && full.result && full.result.output;
                if (raw) {
                    const parsed = JSON.parse('{' + raw + '}');
                    if (parsed.arb) return resolve(parsed.arb);
                }
            } catch(e) {}
            resolve(null);
        }, shader);
    });
}

// This wallet's M of N arbitrator pubkey at the given index.
export async function getMofnKey(arb_index = 0) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=arbitrator,action=get_mofn_key,cid=${CID},arb_index=${arb_index}`;
        Utils.invokeContract(args, (err, result, full) => {
            try {
                const raw = full && full.result && full.result.output;
                if (raw) {
                    const parsed = JSON.parse('{' + raw + '}');
                    const pk = parsed.key && parsed.key.pub_key;
                    if (pk) return resolve(pk);
                }
            } catch(e) {}
            if (err) return reject(new Error(JSON.stringify(err)));
            resolve(result);
        }, shader);
    });
}

// Live registry size N. A dispute freezes this as its quorum base.
export async function viewRegCount() {
    await loadShader();
    return new Promise((resolve) => {
        const args = `role=manager,action=view_regcount,cid=${CID}`;
        Utils.invokeContract(args, (err, result, full) => {
            try {
                const raw = full && full.result && full.result.output;
                if (raw) {
                    const parsed = JSON.parse('{' + raw + '}');
                    if (parsed.regcount) return resolve(Number(parsed.regcount.n_registered) || 0);
                }
            } catch(e) {}
            resolve(0);
        }, shader);
    });
}

export async function voidDispute(job_id) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=void_dispute,cid=${CID},job_id=${job_id}`;
        invokeContract(args, resolve, reject);
    });
}

export async function voidClaimRequester(job_id) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=void_claim_requester,cid=${CID},job_id=${job_id}`;
        invokeContract(args, resolve, reject);
    });
}

export async function voidClaimNode(job_id) {
    await loadShader();
    return new Promise((resolve, reject) => {
        const args = `role=user,action=void_claim_node,cid=${CID},job_id=${job_id}`;
        invokeContract(args, resolve, reject);
    });
}

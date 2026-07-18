# Idios Batch Creation (Operator Guide)

This guide is for operators who need to create multiple Mode B contracts in a single transaction using the Idios agent daemon. It is not a dapp feature and is not aimed at users creating individual contracts through the GUI.

If you are a requester creating one contract at a time, use the dapp. This guide covers a different use case: automated or high-volume systems that need to create many contracts programmatically.

---

## What batch creation is

The Idios app shader has a batch_create_b action that creates up to 50 Mode B contracts in a single on-chain transaction. The wallet aggregates the payment for all contracts into one input set, so instead of N separate transactions locking N separate payments, one transaction locks the sum of all payments at once.

The Idios agent daemon exposes this via a batches key in config. You define your contract specs in config, run the daemon, it fires the batch transaction, confirms all contracts landed on chain, and marks the batch as submitted so it never fires again on restart.

Batch creation and ongoing contract management are two separate steps. The daemon creates the contracts, then you add the resulting job IDs to the jobs list in config for the daemon to manage them through their lifecycle (commit, submit delivery, claim, etc.).

---

## Who this is for

Operators running automated systems: marketplaces, agent pipelines, platforms scheduling work across many workers at once. A human creating two or three contracts has no reason to use this over the dapp. Batch creation pays off when you are creating dozens of contracts at a time from a program or script.

---

## Prerequisites

Everything from the standard daemon setup applies:

- Python 3.10 or later (stdlib only, no pip install needed)
- Beam CLI wallet binary on disk
- Idios app shader (idios_app.wasm) on disk
- A wallet.db holding enough funds to cover the sum of all payments in the batch plus standard Beam network fees (~0.021 BEAM per action)

A Beam node must be reachable before the daemon starts. The daemon spawns the CLI wallet itself, nothing else needs to be running. If you use the embedded node at 127.0.0.1:10005 inside a running Beam Desktop, that Desktop must not share the daemon's wallet.db, they fight over the SQLite lock. For a remote or public node set node_addr accordingly.

---

## The batches config format

Add a top-level batches key alongside jobs in your config.json. It is a list of batch objects. Each batch has a batch_id (a stable string you choose, used for idempotency tracking) and a specs list of contract definitions.

### Required fields per spec

All nine fields are required for every spec entry. The daemon validates these at startup and will exit with an error before prompting for a password if anything is missing or invalid.

| Field | Type | Description |
|---|---|---|
| job_id | integer | Contract ID. Must be unused on chain. You choose this. |
| subnet_id | integer | Subnet identifier for the job. |
| epoch | integer | Epoch for the job. |
| expiry_block | integer | Block height at which the contract expires. Must be in the future. Use current block + a comfortable margin, e.g. current block + 10000 (roughly 7 days). |
| review_window_blocks | integer | How many blocks the requester has to approve or dispute after delivery is submitted. 2000 blocks is roughly 33 hours. |
| payment | integer | Payment amount in groth. 1 BEAM = 100,000,000 groth. For NPH (asset_id 47), same unit. Must be greater than zero. |
| dispute_fee | integer | Fee the requester locks if they dispute. Win or lose, the fee pays the voting arbitrators for judging the dispute. Must be greater than zero. |
| asset_id | integer | Asset ID for the payment. 0 = BEAM, 47 = NPH. |
| node_pk | string | Public key of the worker (Bob) who will commit collateral and deliver. |

### Batch limits

Maximum 50 specs per batch. The daemon validates this at startup and will reject configs that exceed it. The limit comes from the shader: On_user_batch_create_b in the contract enforces nMaxCount = 50 and errors if exceeded.

You can define multiple batches in the batches list. Each fires independently and tracks its own submitted state.

### Funds

The wallet must hold at least the sum of all payment values in the batch. The daemon logs the total before firing:

    batch my_batch_001: firing batch_create_b, count=3, job_ids=[10001, 10002, 10003], total_payment=15000000 groth (wallet must have this available)

Check this line before the wallet approval prompt appears.

---

## Running the daemon

    python3 idios_agent_daemon.py config.json

The daemon prompts for your wallet password once at startup. What happens next, in order:

1. Config validation. The daemon validates all batch specs before doing anything. Missing fields, zero payments, or a batch count over 50 will exit with a clear error message before the password prompt.

2. Args log line. Before the shader call fires, the daemon logs the full args string. Confirm you see job_id_0, payment_0, subnet_id_0 etc. with an underscore before the index. If the format looks wrong, Ctrl-C before the wallet approval.

3. Wallet approval. The Beam wallet prompts for confirmation. The daemon pipes y automatically.

4. On-chain confirmation. After the shader call returns, the daemon polls view_job on the first job ID every 15 seconds, up to 5 attempts (75 seconds total), before marking the batch submitted.

5. Poll loop starts. The daemon enters its normal poll loop for any jobs entries in config.

---

## After the batch lands

The daemon confirms the first job ID landed. Check the remaining job IDs in the dapp to confirm they all exist. They should: the shader creates all N contracts atomically in one transaction.

To manage the contracts through their lifecycle, add the job IDs to the jobs list in config and restart the daemon. It will skip the already-submitted batch and start managing the contracts.

---

## Idempotency

A batch fires at most once. The daemon marks a batch as submitted in jobs-state.json only after both conditions are met: the shader call returns successfully, and view_job confirms the first contract exists on chain.

If the transaction fails for any reason (insufficient funds, network timeout, node unavailable), the batch is not marked submitted and will retry on next daemon start.

If view_job polling times out after 75 seconds but the shader call succeeded, check the dapp to see if the contracts landed. If they did, set batch_submitted_my_batch_001: true in jobs-state.json manually to prevent a resubmit.

On restart with a successfully submitted batch, the daemon logs:

    batch my_batch_001: already submitted (durable state), skipping

---

## Getting the current block height

expiry_block must be in the future. Check the dapp (any contract view shows chain state). Add your desired margin to the current block. 10000 blocks is roughly 7 days at current Beam block times.

---

## Multiple batches

You can define more than one batch in the batches list. Each is processed independently before the poll loop starts. Each has its own submitted flag in state. If one fails, the others still run.

---

## On-chain test reference

Batch creation confirmed working on Beam mainnet against the v3 generation contract (f40eb64da63a69d91afa1a947d9d272a9f80027d7261aa822ec0e4b5827cdc45); batch_create_b is unchanged on the live v2 contract:

| Job IDs | Method | Result |
|---|---|---|
| 99901, 99902 | CLI, manual args | Both landed Open, funds aggregated |
| 99903, 99904 | Daemon batches config | Both landed Open, idempotency confirmed on restart |

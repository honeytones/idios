# idios-agent-daemon

Small Python daemon that automates your role in an Idios contract. Polls the chain, watches your tracked contracts, fires the right contract call when the state machine advances. One-time setup, then walk away.

Supports all three Idios roles: worker, client (requester), and arbitrator. Also supports batch creation: define up to 50 Mode B contracts in config and the daemon fires them as one transaction before the poll loop starts.

Tested end to end against the live v3 contract on Beam mainnet.

---

## Did you arrive here from the Idios dapp?

If you clicked Read more about the daemon or Setup guide from the dapp's MyJobs page, you most likely have a JSON config snippet on your clipboard. Here's what to do with it:

1. Get a copy of this repo on the machine where the daemon will run:

       git clone https://github.com/honeytones/idios.git
       cd idios/idios-agent-daemon

2. Copy the example config and edit:

       cp config.example.json config.json

3. Paste your snippet into the jobs array of config.json. Make sure the other top-level fields point to your local Beam CLI wallet and the Idios app shader.

4. Run the daemon:

       python3 idios_agent_daemon.py config.json

5. Type your wallet password when prompted. Daemon polls every 30s, fires actions when state changes.

If you don't have a Beam CLI wallet set up yet, the Beam wallet downloads page (https://github.com/BeamMW/beam/releases) has the beam-wallet-cli binary. Run ./beam-wallet init once to create a wallet, fund it with enough BEAM to cover gas fees (~0.05 BEAM per action), then point the daemon at the resulting wallet.db.

---

## What it does

For each contract in config.json, the daemon polls view_job on the chain every 30 seconds. When the contract status changes, it fires the next action for the role you configured.

Worker role. Used by the party doing the work (Bob).

| Chain status | Daemon action |
|---|---|
| Open | fires commit with your configured expected_collateral |
| Active | fires submit_delivery with your configured delivery_hash |
| Settled | fires claim for payment + collateral |
| ResolvedToBob (dispute won) | fires claim for payment + collateral + dispute_fee |
| Disputed, Closed, Refunded, ResolvedToAlice | log and idle |

Client role. Used by the party requesting the work (Alice). Auto-approve is opt-in and requires a hash match.

| Chain status | Daemon action |
|---|---|
| AwaitingApproval (Mode B) | if auto_approve_on_hash_match is true AND chain delivery_hash matches expected_delivery_hash, fires approve. Otherwise logs and waits for manual review. |
| ResolvedToAlice (dispute won) | fires claim for payment + dispute_fee |
| Refunded | fires claim for payment |
| Open past expiry, Settled, Closed, Disputed | log and idle |

Arbitrator role. Used by the on-chain arbitrator wallet. Resolves Mode B disputes by hash comparison.

| Chain status | Daemon action |
|---|---|
| Disputed (Mode B, with expected_result_hash set) | compares chain delivery_hash to expected_result_hash. Match resolves to bob (worker wins). Mismatch resolves to alice (client wins). Total payout = payment + collateral + dispute_fee. |
| Disputed (Mode A, or no expected_result_hash) | log and wait for manual operator decision |
| Anything else | log and idle |

Batch creation. Operators creating many Mode B contracts at once can define a batches list in config. The daemon fires all specs as one batch_create_b transaction before the poll loop starts, confirms on chain, and marks each batch submitted in durable state so it never fires again on restart. See the Batch creation section below and docs/idios_batch_creation_operator_guide.md for full details.

---

## Quick start

### Prerequisites

- Python 3.10+ (stdlib only, no pip install required)
- A working Beam CLI wallet binary on disk
- The Idios app shader (idios_app.wasm) somewhere on disk
- A wallet.db with enough BEAM to cover daemon gas fees

### Configure

Copy config.example.json to config.json and edit the paths. Key fields:

    beam_wallet_binary   absolute path to the beam-wallet CLI binary
    shader_app_file      absolute path to idios_app.wasm
    wallet_path          absolute path to your wallet.db
    node_addr            127.0.0.1:10005 for Beam Desktop embedded node
    cid                  ed788e2f03faf0a461d110725509aa49b93671007bb554ea4baea077236ac3cb

node_addr of 127.0.0.1:10005 points at the embedded node inside Beam Desktop. If Beam Desktop is not running you can point at a remote mainnet node like eu-node01.mainnet.beam.mw:8100.

### Run

    python3 idios_agent_daemon.py config.json

The daemon prompts once for your wallet password at startup and holds it in memory for the life of the process. Stop with Ctrl-C. State is saved to jobs-state.json so a restart picks up where it left off without re-firing completed actions.

---

## Config reference

### Top-level fields

| Field | Required | Description |
|---|---|---|
| beam_wallet_binary | yes | absolute path to the beam-wallet CLI binary |
| shader_app_file | yes | absolute path to idios_app.wasm |
| wallet_path | yes | absolute path to your wallet.db |
| node_addr | yes | host:port of the Beam node to connect to |
| cid | yes | contract ID of the Idios deployment to interact with |
| poll_interval_seconds | no | how often to poll view_job (default 30) |
| log_file | no | where to write structured logs (default next to config) |
| state_file | no | where to persist durable state (default next to config) |
| jobs | yes | array of job entries for ongoing contract management |
| batches | no | array of batch definitions for one-shot contract creation |

### Worker job fields

| Field | Required | Description |
|---|---|---|
| job_id | yes | numeric job ID on chain |
| role | yes | must be worker |
| expected_collateral | yes | amount in groth the worker will commit |
| delivery_hash | yes | 32-byte hex hash the worker will submit |

### Client job fields

| Field | Required | Description |
|---|---|---|
| job_id | yes | numeric job ID on chain |
| role | yes | must be client |
| auto_approve_on_hash_match | no | default false. If true, daemon auto-approves when chain delivery_hash matches expected_delivery_hash |
| expected_delivery_hash | yes if auto_approve_on_hash_match is true | 32-byte hex hash the client expects the worker to submit |

### Arbitrator job fields

| Field | Required | Description |
|---|---|---|
| job_id | yes | numeric job ID on chain |
| role | yes | must be arbitrator |
| expected_result_hash | no | 32-byte hex hash. Match resolves to bob. Mismatch resolves to alice. If omitted, daemon logs Disputed contracts and waits for operator decision. |

---

## Batch creation

For operators creating many Mode B contracts at once. Not a dapp feature; this is for automated systems running the daemon.

Add a batches list to config. Each batch has a batch_id string (stable across restarts, used for idempotency) and a specs list of up to 50 contract definitions. All nine spec fields are required: job_id, subnet_id, epoch, expiry_block, review_window_blocks, payment, dispute_fee, asset_id, node_pk.

Each batch fires once. A batch is only marked submitted after the shader call succeeds AND view_job confirms the first contract landed on chain. If either step fails, the batch retries on next daemon start. On restart with an already-submitted batch, the daemon logs 'already submitted (durable state), skipping' and proceeds to the poll loop.

After a batch lands, add the job IDs to the jobs list in config and restart the daemon to manage them through their lifecycle.

For complete details including config examples, the args log line eyeball check, manual state recovery, and multiple batches, see docs/idios_batch_creation_operator_guide.md.

---

## Operational notes

Timeouts. SHADER_TIMEOUT_SECONDS is 600. State-changing calls wait on block confirmation, which on Beam mainnet usually takes 1-2 minutes but occasionally longer. If a transaction times out, the daemon logs an error and the chain state on the next poll tells us whether it landed.

Idempotency. Each daemon action is fired at most once per contract. State is durable in jobs-state.json so daemon restarts do not re-fire completed actions. If a transaction fails on the chain side, the daemon retries on the next poll cycle based on what view_job returns.

Concurrent wallet processes. The daemon shells out to beam-wallet once per shader call. The wallet binary opens wallet.db exclusively. If you have another wallet process open against the same wallet.db (Beam Desktop GUI, another daemon instance), they will fight over the SQLite lock. Run the daemon against a wallet.db nothing else is using.

Security. Wallet password is prompted once at startup, held in process memory only, passed as --pass= to each beam-wallet subprocess. The configured wallet pays gas fees for every contract call. Keep operating funds in a separate wallet and top up as needed.

---

## Known limits (MVP scope)

No HTTP control endpoint. To add or remove contracts at runtime, edit config.json and restart the daemon.

No off-chain transport. The daemon does not pass delivery hashes, output URLs, or decryption keys between parties. That happens off-band. The daemon only handles on-chain settlement automation.

No proactive refund. Client role does not auto-fire refund on expired Open contracts because the daemon has no reliable source of current block height. Manual refund via CLI or dapp is required.

---

## Source

Daemon source: idios_agent_daemon.py (819 lines, single file, stdlib only: sys, os, json, time, logging, getpass, subprocess, re, pathlib, datetime).

Test artifacts on chain (Beam mainnet, contract f40eb64da6...):

| Job ID | Mode | Path tested |
|---|---|---|
| 22227 | A | worker daemon end-to-end |
| 22228 | B | worker + client daemons, hash-match auto-approve |
| 22229 | B | arbitrator daemon, hash-match auto-resolve to BOB |
| 99901, 99902 | B | batch_create_b POC via CLI, funds aggregation confirmed |
| 99903, 99904 | B | batch_create_b via daemon batches config, idempotency confirmed |

For context on how the daemon fits into Idios overall, see the main repo README (https://github.com/honeytones/idios) and the Idios site (https://honeytones.github.io/idios-site/).

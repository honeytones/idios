# idios-agent-daemon

Small Python daemon that automates your role in an Idios contract. Polls the chain, watches your tracked contracts, fires the right contract call when the state machine advances. One-time setup, then walk away.

Supports the two party roles: worker and client (requester). There is no arbitrator role: disputes are resolved by M of N arbitrator voting, a human CLI action the daemon never automates, so a daemon can never rule on a dispute. Also supports batch creation: define up to 50 Mode B contracts in config and the daemon fires them as one transaction before the poll loop starts.

Tested end to end against the live v2 contract surface on Beam mainnet (July 2026).

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
| ResolvedToBob (dispute won) | checks winner_paid via view_dispute, then fires claim for payment + collateral (the dispute fee goes to the voting arbitrators). Never retries a paid claim: a Resolved job keeps its status forever and winner_paid is the paid signal |
| Disputed | waits for the arbitrator vote; fires void_dispute once the arbitrator timeout passes |
| Voided | fires void_claim_node to reclaim the collateral |
| Closed, Refunded, ResolvedToAlice, Cancelled | log and idle |

Client role. Used by the party requesting the work (Alice). Auto-approve is opt-in and requires a hash match.

| Chain status | Daemon action |
|---|---|
| Open or Active past expiry | if auto_refund_after_expiry is true, fires refund (on the Active path the worker collateral forfeits to the treasury). Otherwise idles |
| AwaitingApproval (Mode B) | if auto_approve_on_hash_match is true AND chain delivery_hash matches expected_delivery_hash, fires approve. Otherwise logs and waits for manual review |
| ResolvedToAlice (dispute won) | checks winner_paid via view_dispute, then fires claim for payment + collateral (the dispute fee goes to the voting arbitrators). Never retries a paid claim |
| Disputed | waits for the arbitrator vote; fires void_dispute once the arbitrator timeout passes |
| Voided | fires void_claim_requester to reclaim the payment |
| Refunded | terminal, the refund tx already returned the payment, nothing to claim |
| Settled, Closed, ResolvedToBob, Cancelled | log and idle |

Batch creation. Operators creating many Mode B contracts at once can define a batches list in config. The daemon fires all specs as one batch_create_b transaction before the poll loop starts, confirms on chain, and marks each batch submitted in durable state so it never fires again on restart. See the Batch creation section below and ../docs/idios_batch_creation_operator_guide.md for full details.

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
    node_addr            eu-node01.mainnet.beam.mw:8100 (public mainnet node)
    cid                  41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f

The cid above is the live Idios v2 contract on Beam mainnet. eu-node01 is a public node; run your own for anything serious, or use 127.0.0.1:10005 for the embedded node inside a running Beam Desktop.

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
| auto_refund_after_expiry | no | default false. If true, daemon fires refund on an Open or Active job once expiry_block passes. On the Active path the worker collateral forfeits to the treasury, so only enable this if that is the intended remedy for non delivery |

---

## Batch creation

For operators creating many Mode B contracts at once. Not a dapp feature; this is for automated systems running the daemon.

Add a batches list to config. Each batch has a batch_id string (stable across restarts, used for idempotency) and a specs list of up to 50 contract definitions. All nine spec fields are required: job_id, subnet_id, epoch, expiry_block, review_window_blocks, payment, dispute_fee, asset_id, node_pk.

Each batch fires once. A batch is only marked submitted after the shader call succeeds AND view_job confirms the first contract landed on chain. If either step fails, the batch retries on next daemon start. On restart with an already-submitted batch, the daemon logs 'already submitted (durable state), skipping' and proceeds to the poll loop.

After a batch lands, add the job IDs to the jobs list in config and restart the daemon to manage them through their lifecycle.

For complete details including config examples, the args log line eyeball check, manual state recovery, and multiple batches, see ../docs/idios_batch_creation_operator_guide.md.

---

## Operational notes

Timeouts. SHADER_TIMEOUT_SECONDS is 600. State-changing calls wait on block confirmation, which on Beam mainnet usually takes one to two minutes, occasionally several. If a transaction times out, the daemon logs an error and the chain state on the next poll tells us whether it landed.

Idempotency. Each daemon action is fired at most once per contract. State is durable in jobs-state.json so daemon restarts do not re-fire completed actions. If a transaction fails on the chain side, the daemon retries on the next poll cycle based on what view_job returns.

Concurrent wallet processes. The daemon shells out to beam-wallet once per shader call. The wallet binary opens wallet.db exclusively. If you have another wallet process open against the same wallet.db (Beam Desktop GUI, another daemon instance), they will fight over the SQLite lock. Run the daemon against a wallet.db nothing else is using.

Security. Wallet password is prompted once at startup, held in process memory only, passed as --pass= to each beam-wallet subprocess. The configured wallet pays gas fees for every contract call. Keep operating funds in a separate wallet and top up as needed.

---

## Known limits (MVP scope)

No HTTP control endpoint. To add or remove contracts at runtime, edit config.json and restart the daemon.

No off-chain transport. The daemon does not pass delivery hashes, output URLs, or decryption keys between parties. That happens off-band. The daemon only handles on-chain settlement automation.

Refund is opt in. The daemon reads the chain height once per poll cycle and fires refund on an expired Open or Active job only when auto_refund_after_expiry is set, because the Active path forfeits the worker's collateral to the treasury and that should be a deliberate choice.

---

## Source

Daemon source: idios_agent_daemon.py (single file, stdlib only: sys, os, json, time, logging, getpass, subprocess, re, pathlib, datetime).

Test artifacts on chain (Beam mainnet):

| Job ID | Mode | Contract | Path tested |
|---|---|---|---|
| 73952 | B | v2 test cid (July 2026) | worker + client daemon in one process, unattended Open to Closed in under 5 minutes: commit, submit, hash-match auto-approve, claim |
| 22227 | A | v3 (f40eb64da6...) | worker daemon end-to-end |
| 22228 | B | v3 | worker + client daemons, hash-match auto-approve |
| 99901, 99902 | B | v3 | batch_create_b POC via CLI, funds aggregation confirmed |
| 99903, 99904 | B | v3 | batch_create_b via daemon batches config, idempotency confirmed |

For context on how the daemon fits into Idios overall, see the main repo README (https://github.com/honeytones/idios) and the Idios site (https://honeytones.github.io/idios-site/).

# idios-agent-daemon

Small Python daemon that automates your role in an Idios job. Polls the chain, watches your tracked jobs, fires the right contract call when the state machine advances. One-time setup, then walk away.

Supports all three Idios roles: **worker**, **client** (requester), and **arbitrator**. Tested end to end against the live v3 contract on Beam mainnet.

---

## Did you arrive here from the Idios dapp?

If you clicked **Read more about the daemon** or **Setup guide** from the dapp's MyJobs page, you most likely have a JSON config snippet on your clipboard. Here's what to do with it:

1. **Get a copy of this repo** on the machine where the daemon will run (the same machine you have Beam CLI wallet running on):
   ```bash
   git clone https://github.com/honeytones/idios.git
   cd idios/idios-agent-daemon
   ```
2. **Copy the example config** and edit:
   ```bash
   cp config.example.json config.json
   ```
3. **Paste your snippet** into the `jobs` array of `config.json`. Make sure the other top-level fields (`beam_wallet_binary`, `shader_app_file`, `wallet_path`, etc.) point to your local Beam CLI wallet and the Idios app shader.
4. **Run the daemon**:
   ```bash
   python3 idios_agent_daemon.py config.json
   ```
5. Type your wallet password when prompted. Daemon polls every 30s, fires actions when state changes.

If you don't have a Beam CLI wallet set up yet, the [Beam wallet downloads page](https://github.com/BeamMW/beam/releases) has the `beam-wallet-cli` binary. Run `./beam-wallet init` once to create a wallet, fund it with enough BEAM to cover gas fees for daemon actions (~0.05 BEAM per action), then point the daemon at the resulting `wallet.db`.

---

## What it does

For each job in `config.json`, the daemon polls `view_job` on the chain every 30 seconds. When the job's status changes, it fires the next action for the role you configured.

**Worker role.** Used by the party doing the work (Bob).

| Chain status | Daemon action |
|---|---|
| Open | fires `commit` with your configured `expected_collateral` |
| Active | fires `submit_delivery` with your configured `delivery_hash` |
| Settled | fires `claim` for payment + collateral |
| ResolvedToBob (dispute won) | fires `claim` for payment + collateral + dispute_fee |
| Disputed, Closed, Refunded, ResolvedToAlice | log and idle |

**Client role.** Used by the party requesting the work (Alice). Auto-approve is opt-in and requires a hash match.

| Chain status | Daemon action |
|---|---|
| AwaitingApproval (Mode B) | if `auto_approve_on_hash_match: true` AND chain `delivery_hash` matches your `expected_delivery_hash`, fires `approve`. Otherwise logs and waits for manual review. |
| ResolvedToAlice (dispute won) | fires `claim` for payment + dispute_fee |
| Refunded | fires `claim` for payment |
| Open past expiry, Settled, Closed, Disputed | log and idle |

**Arbitrator role.** Used by the on-chain arbitrator wallet. Resolves Mode B disputes by hash comparison.

| Chain status | Daemon action |
|---|---|
| Disputed (Mode B, with `expected_result_hash` set) | compares chain `delivery_hash` to your `expected_result_hash`. Match → `resolve_bob` (worker wins). Mismatch → `resolve_alice` (client wins). Total payout = payment + collateral + dispute_fee in either case. |
| Disputed (Mode A, or no `expected_result_hash`) | log and wait for manual operator decision |
| Anything else | log and idle |

---

## Quick start

### Prerequisites

- Python 3.10+ (stdlib only, no `pip install` required)
- A working Beam CLI wallet binary on disk
- The Idios app shader (`idios_app.wasm`) somewhere on disk
- A wallet.db with enough BEAM to cover daemon gas fees

### Configure

Copy `config.example.json` to `config.json` and edit the paths:

```json
{
  "beam_wallet_binary": "/home/you/beam-cli/beam-wallet",
  "shader_app_file": "/path/to/idios_app.wasm",
  "wallet_path": "/home/you/beam-cli/wallet.db",
  "node_addr": "127.0.0.1:10005",
  "cid": "f40eb64da63a69d91afa1a947d9d272a9f80027d7261aa822ec0e4b5827cdc45",
  "poll_interval_seconds": 30,
  "log_file": "./idios-daemon.log",
  "state_file": "./jobs-state.json",
  "jobs": [
    {
      "job_id": 12345,
      "role": "worker",
      "expected_collateral": 2000000,
      "delivery_hash": "deadbeef00000000000000000000000000000000000000000000000000000000"
    }
  ]
}
```

`node_addr` of `127.0.0.1:10005` points at the embedded node inside Beam Desktop. If Beam Desktop isn't running you can point at a remote mainnet node like `eu-node01.mainnet.beam.mw:8100`.

### Run

```bash
python3 idios_agent_daemon.py config.json
```

The daemon prompts once for your wallet password at startup and holds it in memory for the life of the process. Password is passed to each `beam-wallet` subprocess as `--pass=<password>`, never written to disk.

Stop with Ctrl-C. State is saved to `jobs-state.json` so a restart picks up where it left off without re-firing completed actions.

---

## Config reference

### Top-level fields

| Field | Required | Description |
|---|---|---|
| `beam_wallet_binary` | yes | absolute path to the `beam-wallet` CLI binary |
| `shader_app_file` | yes | absolute path to `idios_app.wasm` |
| `wallet_path` | yes | absolute path to your `wallet.db` |
| `node_addr` | yes | host:port of the Beam node to connect to |
| `cid` | yes | contract ID of the Idios deployment to interact with |
| `poll_interval_seconds` | no | how often to poll view_job (default 30) |
| `log_file` | no | where to write structured logs (default next to config) |
| `state_file` | no | where to persist durable job state (default next to config) |
| `jobs` | yes | array of job entries |

### Worker job fields

| Field | Required | Description |
|---|---|---|
| `job_id` | yes | numeric job ID on chain |
| `role` | yes | must be `"worker"` |
| `expected_collateral` | yes | amount in groth (1 BEAM = 100,000,000 groth) the worker will commit |
| `delivery_hash` | yes | 32-byte hex hash the worker will submit. For Mode A this must match the requester's `result_hash`. For Mode B this is what the client will compare against. |

### Client job fields

| Field | Required | Description |
|---|---|---|
| `job_id` | yes | numeric job ID on chain |
| `role` | yes | must be `"client"` |
| `auto_approve_on_hash_match` | no | default false. If true, daemon will auto-approve when chain `delivery_hash` matches `expected_delivery_hash` |
| `expected_delivery_hash` | yes (if `auto_approve_on_hash_match` is true) | 32-byte hex hash the client expects the worker to submit |

### Arbitrator job fields

| Field | Required | Description |
|---|---|---|
| `job_id` | yes | numeric job ID on chain |
| `role` | yes | must be `"arbitrator"` |
| `expected_result_hash` | no | 32-byte hex hash. Match → resolve_bob. Mismatch → resolve_alice. If omitted, daemon logs Disputed jobs and waits for operator decision. |

---

## Operational notes

### Timeouts

`SHADER_TIMEOUT_SECONDS` is 600. State-changing calls (commit, submit_delivery, approve, claim, resolve_alice/bob) wait on block confirmation, which on Beam mainnet usually takes 1-2 minutes per block but occasionally runs longer. 600 seconds is generous; if a transaction takes longer than that, the daemon logs an error and the chain state on the next poll tells us whether the transaction actually landed.

### Idempotency

Each daemon action is fired at most once per job. State is durable in `jobs-state.json` so daemon restarts don't re-fire completed actions. If a transaction fails on the chain side (rejection, timeout), the daemon retries on the next poll cycle based on what `view_job` returns.

### Concurrent wallet processes

The daemon shells out to `beam-wallet` once per shader call. The wallet binary opens `wallet.db` exclusively. If you have another wallet process open against the same `wallet.db` (the Beam Desktop GUI, another daemon, a `beam-wallet listen` session), they will fight over the SQLite lock and one will fail. Run the daemon against a wallet.db nothing else is using.

### Security

- Wallet password is prompted once at startup, held in process memory only, passed as `--pass=` to each `beam-wallet` subprocess. Visible to root via `/proc/<pid>/cmdline` like any subprocess arg, otherwise not on disk.
- The configured wallet pays gas fees for every contract call. Don't point the daemon at a wallet that holds your whole stack. Keep operating funds in a separate wallet and top up as needed.
- The `expected_result_hash` and `expected_delivery_hash` fields encode your off-chain agreement with the counterparty. The daemon trusts them. If they're wrong, the daemon will fire the wrong action.

---

## Known limits (MVP scope)

These are honest gaps, not bugs. They're next-iteration work:

- **No HTTP control endpoint.** To add or remove jobs at runtime, edit config.json and restart the daemon. The v4 design doc spec'd an HTTP endpoint for this but it isn't in the MVP.
- **No off-chain transport.** The daemon doesn't pass delivery hashes, output URLs, or decryption keys between parties. That has to happen off-band via SBBS, IPFS, email, whatever the parties agree on. The daemon only handles the on-chain settlement automation.
- **Mode A client role doesn't have auto-actions.** Mode A is the auto-settling hash-verified mode, so the client has nothing to do after creating the job; the worker's submit_delivery either matches the result_hash and auto-settles, or doesn't and the job sits at Active. Worker daemon handles this fine.
- **No proactive refund.** Client role logs but doesn't auto-fire refund on expired Open jobs, because the daemon doesn't have a reliable source of current block height. Manual refund via CLI or dapp is still required.

---

## Source

Daemon source: [`idios_agent_daemon.py`](./idios_agent_daemon.py) (~500 lines, single file, stdlib only).

Test artifacts on chain (Beam mainnet, contract `f40eb64da6...`):

| Job ID | Mode | Roles tested |
|---|---|---|
| 22227 | A | worker daemon end-to-end |
| 22228 | B | worker + client daemons, hash-match auto-approve |
| 22229 | B | arbitrator daemon, hash-match auto-resolve to BOB |

For context on how the daemon fits into Idios overall, see the [main repo README](https://github.com/honeytones/idios#architecture-notes) and the [Idios site](https://honeytones.github.io/idios-site/).

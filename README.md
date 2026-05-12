# Idios

**Private escrow and settlement for AI and other compute on Beam.**

Pay for AI and compute work privately. Verifiable delivery, escrowed payment, on-chain dispute resolution. No public record of amounts or parties.

**[Website](https://honeytones.github.io/idios-site/)** · **[Latest Release](https://github.com/honeytones/idios/releases/latest)** · **[Live Explorer](https://explorer.0xmx.net/?network=mainnet&type=contract&id=f40eb64da63a69d91afa1a947d9d272a9f80027d7261aa822ec0e4b5827cdc45)**

> "Pay privately. Verify on chain. Dispute when needed."

---

## Why

Public payment rails leak. Every job, every payment, every counterparty becomes part of a permanent searchable record. For AI inference, model training, scientific compute, work that involves proprietary inputs, private data, or competitive operations, that visibility is a dealbreaker.

Idios solves the payment and settlement privacy problem. Payment is locked in private escrow on [Beam](https://beam.mw). The worker locks collateral as a performance bond. Settlement happens on chain with full privacy of amounts and parties. Beam's MimbleWimble protocol hides amounts and identities at the base layer. Idios is escrow built on top.

The primitive Idios implements is the same one ERC-8183 standardises on Ethereum for AI agent commerce: a Job with a Client, a Provider, and an Evaluator, with payment held in escrow until verifiable delivery. ERC-8183 implementations on Ethereum, Base, and BNB Chain are public by default. Idios is the private equivalent on Beam.

---

## How it works

Two settlement modes, picked per job at creation time.

### Hash-verified Settlement (Mode A)

For deterministic work where the correct output is a specific hash known in advance.

1. Requester locks payment, declares the expected result hash.
2. Worker locks collateral.
3. Worker delivers work and submits the result hash on chain.
4. If hashes match, the contract atomically releases payment plus collateral to the worker. Done.

No third party involved. Trustless when the hash is right.

### Reviewed Settlement (Mode B)

For non deterministic or open ended work where the requester needs to review the output.

1. Requester locks payment, sets a review window and dispute fee.
2. Worker locks collateral.
3. Worker delivers work (uploaded out of band or via IPFS, hash recorded on chain).
4. Requester reviews the work and either:
   - **Approves**, allowing the worker to claim payment + collateral.
   - **Disputes**, locking the dispute fee and pushing the case to arbitration.
   - **Does nothing** until the review window expires, after which the worker can claim payment + collateral via timeout.
5. If disputed, an on-chain arbitrator resolves to either party. Winner claims the full pot (payment + collateral + dispute fee).

Funds always flow to the right party. The arbitrator can decide who wins a dispute but never receives the funds themselves.

### Two phase claim

Idios v3 uses a two phase claim pattern. Authorisation methods (approve, resolve_alice, resolve_bob, claim_after_timeout) set the job status but never move funds. The beneficiary then calls `claim` to actually receive the payout, signed by their own key. This works around a Beam BVM constraint where a single kernel cannot cleanly sign for one party while routing funds to another.

---

## Status

**Live on Beam mainnet** ✅

- v3 contract deployed at block 3842196 (May 2, 2026)
- All four Mode B resolution paths verified end to end with real funds
- Dapp v3.0.6 published, supports both modes via UI

**Verified resolution paths (real funds, mainnet):**

| Path | Status flow | Job |
|------|-------------|-----|
| Mode A hash match | Open → Active → Settled (single tx) | 22224 |
| Mode B approve | Open → Active → AwaitingApproval → Settled → Closed | 11112, 33335 |
| Mode B dispute, resolved to worker | Open → Active → AwaitingApproval → Disputed → ResolvedToBob → Closed | 11113 |
| Mode B dispute, resolved to requester | Open → Active → AwaitingApproval → Disputed → ResolvedToAlice → Closed | 11114 |
| Mode B timeout | Open → Active → AwaitingApproval → Settled → Closed | 11115 |

---

## Contract

```
CID: f40eb64da63a69d91afa1a947d9d272a9f80027d7261aa822ec0e4b5827cdc45
Deployed at block: 3842196
Constructor params: default_review_window=10080, arbitrator_timeout_blocks=20160
Explorer: https://explorer.0xmx.net/?network=mainnet&type=contract&id=f40eb64da63a69d91afa1a947d9d272a9f80027d7261aa822ec0e4b5827cdc45
```

### Roles

| Role | Description |
|------|-------------|
| `user` | Requester (Alice) or worker (Bob) interacting with the job lifecycle |
| `arbitrator` | Single on-chain arbitrator (set at deploy) who can resolve disputes |
| `manager` | Deploy and view contract params (one-shot, used during contract setup) |

### Actions per role

**user actions**

| Action | Description |
|--------|-------------|
| `create_a` | Create a Mode A job (Hash-verified Settlement). Locks payment. |
| `create_b` | Create a Mode B job (Reviewed Settlement). Locks payment, sets review window and dispute fee. |
| `commit` | Worker locks collateral, status moves to Active. |
| `submit_delivery` | Worker submits result hash. In Mode A, settles atomically if hash matches. In Mode B, sets AwaitingApproval. |
| `approve` | Requester approves Mode B delivery, status moves to Settled (no funds yet). |
| `dispute` | Requester disputes Mode B delivery, locks dispute fee, status moves to Disputed. |
| `claim_after_timeout` | Worker claims after review window expires (Mode B), status moves to Settled. |
| `claim` | Beneficiary collects funds from a Settled, ResolvedToBob, or ResolvedToAlice job. Status moves to Closed. |
| `refund` | Requester reclaims funds from an expired Open job. Status moves to Refunded. |
| `view_job` | Read current job state. |
| `get_key` | Returns the user's pubkey for this contract. (Worker shares this with requester before create.) |

**arbitrator actions**

| Action | Description |
|--------|-------------|
| `resolve_alice` | Resolve a Disputed job in favour of the requester. Status moves to ResolvedToAlice. |
| `resolve_bob` | Resolve a Disputed job in favour of the worker. Status moves to ResolvedToBob. |
| `get_key` | Returns the arbitrator's pubkey for this contract. |

### Status codes

```
0 = Open               (just created, awaiting worker commit)
1 = Active             (worker has committed collateral)
2 = AwaitingApproval   (Mode B, worker has delivered, in review window)
3 = Disputed           (Mode B, requester has disputed, awaiting arbitrator)
4 = Settled            (worker can claim payment + collateral)
5 = Refunded           (terminal, requester reclaimed funds after expiry)
6 = ResolvedToAlice    (arbitrator sided with requester, requester can claim)
7 = ResolvedToBob      (arbitrator sided with worker, worker can claim)
8 = Closed             (terminal, claim has been collected)
```

---

## Quick start (dapp UI)

The simplest way to use Idios is through the Beam Desktop wallet's dapp store.

1. Install the Beam Desktop wallet for your platform from [beam.mw](https://beam.mw)
2. Sync to mainnet and fund with a small amount of BEAM. BEAM is on Kraken, Gate, MEXC, CoinEx. For a quick swap from ETH to BEAM without an exchange account, [buybeam.my](https://buybeam.my/) is a community-run service.
3. Install the Idios dapp from the wallet's DApp Store, or sideload the latest [`.dapp` file from releases](https://github.com/honeytones/idios/releases/latest).
4. Open Idios from your installed apps.

The dapp opens to a landing page with three entry points:

- **Start a job**: As a requester, fill in deal terms (job ID, payment, expiry, worker pubkey). Choose Hash-verified Settlement (upload deliverable file, dapp computes SHA-256 hash locally) or Reviewed Settlement (set review window and dispute fee). Create the job. If you arrived via an offer link from a worker, the form auto-fills.
- **Generate a job offer**: As a worker, fill in the agreed deal terms, upload your finished deliverable (Mode A) or set review settings (Mode B), and click Generate Offer. Produces a shareable text block and link for sending to the requester.
- **My jobs**: See live status of every job tracked locally. Action buttons appear conditionally: Refund expired jobs, Approve or Dispute Mode B deliveries, Claim Funds when a job is Settled or Resolved in your favour.

> Note on expiry block: Beam mainnet produces a block roughly every 60 seconds. Set `expiry_block` to at least `current_block + 200` to give the create transaction time to confirm before expiry. For real jobs, `current_block + 1440` (~24 hours) is more typical.

---

## CLI usage

The Beam CLI wallet drives the contract directly. Useful for scripting, building integrations, or any role beyond what the dapp exposes.

### Prerequisites

- Beam CLI wallet binary, available from the [Beam releases page](https://github.com/BeamMW/beam/releases)
- A copy of `idios_app.wasm` (downloadable from this repo or built from source, see [Build](#build))
- A Beam mainnet node to connect to. Run your own, or use a public node like `eu-node01.mainnet.beam.mw:8100`.

All examples use `cid=f40eb64d...` (v3) and a public node. Substitute your own as needed.

### Get worker pubkey for this contract

The worker's pubkey is contract-specific (because the CID is part of the key derivation). Before any create, the worker runs:

```bash
./beam-wallet shader \
  --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=get_key,cid=f40eb64da63a69d91afa1a947d9d272a9f80027d7261aa822ec0e4b5827cdc45" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

The output is the worker's `node_pk` for that contract. Send this to the requester.

### View a job

```bash
./beam-wallet shader \
  --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=view_job,cid=f40eb64da63a69d91afa1a947d9d272a9f80027d7261aa822ec0e4b5827cdc45,job_id=<N>" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

### Create a Mode A job (Hash-verified Settlement)

```bash
./beam-wallet shader \
  --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=create_a,cid=f40eb64da63a69d91afa1a947d9d272a9f80027d7261aa822ec0e4b5827cdc45,job_id=<N>,subnet_id=1,epoch=1,expiry_block=<FUTURE>,payment=<GROTH>,asset_id=0,node_pk=<WORKER_PUBKEY>,result_hash=<HASH>" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

Payment is in groth (1 BEAM = 100,000,000 groth). For example `payment=5000000` is 0.05 BEAM.

### Create a Mode B job (Reviewed Settlement)

```bash
./beam-wallet shader \
  --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=create_b,cid=f40eb64da63a69d91afa1a947d9d272a9f80027d7261aa822ec0e4b5827cdc45,job_id=<N>,subnet_id=1,epoch=1,expiry_block=<FUTURE>,review_window_blocks=<N>,payment=<GROTH>,dispute_fee=<GROTH>,asset_id=0,node_pk=<WORKER_PUBKEY>" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

### Commit collateral (worker)

```bash
./beam-wallet shader \
  --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=commit,cid=f40eb64da63a69d91afa1a947d9d272a9f80027d7261aa822ec0e4b5827cdc45,job_id=<N>,collateral=<GROTH>,asset_id=0" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

### Submit delivery (worker)

```bash
./beam-wallet shader \
  --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=submit_delivery,cid=f40eb64da63a69d91afa1a947d9d272a9f80027d7261aa822ec0e4b5827cdc45,job_id=<N>,delivery_hash=<HASH>,mode=<65|66>,payment=<GROTH>,collateral=<GROTH>,asset_id=0" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

`mode=65` for Mode A (ASCII 'A'), `mode=66` for Mode B (ASCII 'B').

### Mode B: approve, dispute, claim_after_timeout

```bash
# Requester approves
./beam-wallet shader --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=approve,cid=f40eb64da63a69d91afa1a947d9d272a9f80027d7261aa822ec0e4b5827cdc45,job_id=<N>" \
  --node_addr=eu-node01.mainnet.beam.mw:8100

# Requester disputes
./beam-wallet shader --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=dispute,cid=f40eb64da63a69d91afa1a947d9d272a9f80027d7261aa822ec0e4b5827cdc45,job_id=<N>,dispute_fee=<GROTH>,asset_id=0" \
  --node_addr=eu-node01.mainnet.beam.mw:8100

# Worker claims after review window expires
./beam-wallet shader --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=claim_after_timeout,cid=f40eb64da63a69d91afa1a947d9d272a9f80027d7261aa822ec0e4b5827cdc45,job_id=<N>" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

### Arbitrator: resolve a dispute

The arbitrator wallet is the one that originally deployed the contract.

```bash
# Resolve in favour of requester
./beam-wallet shader --shader_app_file=idios_app.wasm \
  --shader_args="role=arbitrator,action=resolve_alice,cid=f40eb64da63a69d91afa1a947d9d272a9f80027d7261aa822ec0e4b5827cdc45,job_id=<N>" \
  --node_addr=eu-node01.mainnet.beam.mw:8100

# Resolve in favour of worker
./beam-wallet shader --shader_app_file=idios_app.wasm \
  --shader_args="role=arbitrator,action=resolve_bob,cid=f40eb64da63a69d91afa1a947d9d272a9f80027d7261aa822ec0e4b5827cdc45,job_id=<N>" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

### Claim funds (beneficiary)

After Settled, ResolvedToBob, or ResolvedToAlice, the beneficiary calls `claim`. Total is the full payout amount. For Settled it's payment + collateral. For Resolved* it's payment + collateral + dispute_fee.

```bash
./beam-wallet shader --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=claim,cid=f40eb64da63a69d91afa1a947d9d272a9f80027d7261aa822ec0e4b5827cdc45,job_id=<N>,total=<GROTH>,asset_id=0" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

### Refund (requester, after expiry)

For an Open job whose `expiry_block` has passed without anyone committing.

```bash
./beam-wallet shader --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=refund,cid=f40eb64da63a69d91afa1a947d9d272a9f80027d7261aa822ec0e4b5827cdc45,job_id=<N>,payment=<GROTH>,collateral=<GROTH>,asset_id=0" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

---

## Repo structure

```
idios_contract.h       Contract Shader header (job struct, status enum, method IDs)
idios_contract.cpp     Contract Shader (on chain logic, escrow, claim, dispute resolution)
idios_app.cpp          App Shader (wallet-side transaction builder)
idios_contract.wasm    Compiled contract (loaded on chain at deploy)
idios_app.wasm         Compiled app shader (used by wallet to construct kernels)
build_v2.sh            Build script
idios-dapp-src/        Mirror of the dapp source (BeamMW template fork)
```

---

## Build

The contract and app shaders are written in C++ and built with the Beam Shader SDK.

```bash
bash build_v2.sh
```

Produces `idios_contract.wasm` (~4.5 KB) and `idios_app.wasm` (~11 KB).

---

## Architecture notes

**Two phase claim.** Authorisation methods (approve, resolve_alice, resolve_bob, claim_after_timeout) set the job status. The beneficiary then calls Method_15 Claim signed with their own key to actually receive the funds. This works around a Beam BVM constraint where one kernel can't cleanly sign for one party while routing funds to a different party.

**Single on-chain arbitrator (today).** The arbitrator pubkey is set at deploy time from the deploying wallet. They can resolve disputes but cannot receive funds (the contract enforces FundsUnlock to the winning party, not to the arbitrator). M of N multi-arbitrator resolution is Phase 1 of the roadmap.

**Contract-specific keys.** Every party derives their pubkey using `Env::DerivePk` with the contract ID as part of the input. A worker's pubkey on contract A is different from their pubkey on contract B. Always run `get_key` on the target contract before passing `node_pk` into create.

**Refund semantics.** `refund` only works for jobs that never had a worker commit (status=Open) and whose `expiry_block` has passed. Once a worker has committed, refund is no longer available. The job must complete one of the resolution paths instead.

**No bridge, no cross chain.** Idios runs entirely on Beam mainnet. No wrapped assets, no second chain.

---

## Roadmap

**Phase 1: Multi-arbitrator dispute resolution**

- [ ] Multi-arbitrator key registry in the contract
- [ ] M of N signature requirement on resolve_alice and resolve_bob
- [ ] Coordination layer for arbitrators to communicate (Beam SBBS or compatible)
- [ ] First multi-arbitrator dispute resolution on mainnet

**Phase 1.5: ERC-8183 semantic alignment**

- [ ] Rename contract field references from Requester/Worker/Arbitrator to Client/Provider/Evaluator in docs and dapp UI
- [ ] Document Job lifecycle vocabulary (Open, Funded, Submitted, Terminal) alongside Idios native status codes
- [ ] Reference implementation in v4 contract release notes

**Phase 2: Payload delivery**

- [ ] Job specifications and deliverables sent via IPFS through Beam's private IPFS network
- [ ] Encrypted payloads with keys exchanged out of band
- [ ] Larger work files supported beyond what fits in a hash

**Phase 3: Verification beyond deterministic and human review**

- [ ] Programmatic verifiers for specific job classes (numerical bounds, schema match, etc)
- [ ] Public verifier directory
- [ ] Verifier reputation tracking

**Other directions under consideration**

- [ ] Asset support beyond BEAM (Nephrite asset_id=47 etc)
- [ ] Native dapp arbitrator dashboard

---

## Contributing

Idios is early and moving fast. If you're building on Beam and want to integrate, open an issue or reach out directly.

Built by the community, for the community.

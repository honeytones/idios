# Idios

**Private escrow and settlement layer for AI compute on Beam.**

Pay for AI work privately. Verifiable delivery, escrowed payment, no public record of amounts or identities.

> "Run AI on sensitive data privately, verifiably, without trusting any single party."

---

## Why

Public payment rails leak everything. Who hires whom, what they pay, how often. For anyone running sensitive work or operating in regulated industries, that visibility is a dealbreaker regardless of how capable the underlying AI is.

Idios solves the payment and settlement privacy problem. Payment is locked in private escrow on [Beam](https://beam.mw). The node providing the work locks collateral as a performance bond. Verification happens independently of any public chain. None of it appears on a public ledger.

---

## How verification works

Verification is what makes the escrow trustworthy. Without it, settle and slash decisions would rest on a single party.

The path forward is a multi operator network. Operators run Beam nodes and automated verification scripts. Each operator independently checks whether the work was delivered correctly. Settlement requires multiple operators to agree, enforced by Beam multi signature at the contract level.

For deterministic work where the correct output is known in advance, verification is automatic. Operators compare hashes and sign the result. For non deterministic work, verification design is open and depends on the use case.

---

## Where this is going

The longer term goal is for Idios to operate as a [Hypertensor](https://hypertensor.org) subnet, so that anyone can run an Idios operator node and earn rewards through standard subnet economics. This decentralises verification fully and aligns operator incentives with network quality.

That requires Hypertensor mainnet to be live, which is its own timeline. Following guidance from the Hypertensor team, the multi operator network is being built standalone first, with subnet integration as a later phase. The escrow contract on Beam works independently of Hypertensor and operates today.

Operator economics for the standalone phase are still being worked out. The basic structure is fees per settled job paid to participating operators. Real numbers will come from running the network with a small group of operators and observing what works.

---

## How it works

Idios runs entirely on Beam. The contract handles escrow, payment, and slashing. A small group of independent operator scripts handle verification and trigger settlement decisions. Hypertensor integration is a future phase.

```
Requester                Operators              Node
    │                         │                     │
    ├─── create job ──────────►│                     │
    │    (locks BEAM escrow)   │                     │
    │                         │                     │
    │                         │  job spec via SBBS  │
    │  ─────────────────────────────────────────────►│
    │                         │                     │
    │                         │       inference     │
    │                         │  ◄──────────────────┤
    │                         │                     │
    │                         │ verify + sign       │
    │                         │ M of N attestations │
    │                         │                     │
    │                         ├─── settle ──────────► Beam contract
    │                              (payment releases privately)
```

**Three components:**

- **Beam contract** Private escrow, payment release, slashing, multi signature settlement
- **Operator network** Independent verifiers running automated scripts, signing settle and slash decisions
- **Hypertensor** Future integration as a subnet for permissionless operator participation and stake weighted consensus

**Job lifecycle:**

1. Requester calls `create` and locks payment, specifies node pubkey and result hash
2. Node calls `commit` and locks collateral, job moves to Active
3. Requester sends the actual work specification to the node (via SBBS or out of band)
4. Node performs the work and returns the result
5. Operators verify the result independently (hash match for deterministic jobs)
6. Once M of N operators agree, settle releases payment to the node, or slash burns collateral and refunds the requester
7. If no operator agreement is reached before expiry, the requester can refund directly

---

## Status

**Confirmed working on Beam mainnet** ✅

- Custom Contract Shader deployed (job create / commit / settle / slash / refund)
- Full job lifecycle tested end to end on mainnet across multiple jobs
- Two wallet flow proven: requester locks, node commits, middleware settles
- Python middleware wired to Beam Wallet API (invoke_contract + process_invoke_data)
- Dapp UI for end users (create / view / refund) validated against the live contract

**Working today:**

- **Fast Settlement (deterministic).** The requester commits a result hash at job creation. The node delivers matching output and middleware triggers settle. This path uses only the Beam contract and is fully validated on mainnet.

**In design:**

- **Epoch Settlement (open ended tasks).** A consensus mechanism among middleware operators that decides settle or slash for jobs without a predetermined result hash. Per Hypertensor team guidance, the path is to build this with multiple peers first, then integrate as a Hypertensor subnet later. Currently a single operator middleware exists but multi operator consensus is not yet built.

---

## Contract

Deployed on Beam mainnet:

```
CID: 74c497b7fe906c09e0da91d1a5e43b2afe122b1a6af3ae74c9440259d6f27027
Deployed height: 3832296
```

**Actions:**

| Role | Action | Description |
|------|--------|-------------|
| `user` | `create` | Lock payment, specify node and result hash |
| `user` | `commit` | Node locks collateral, job goes Active |
| `middleware` | `settle` | Release payment + return collateral (job passed) |
| `middleware` | `slash` | Burn collateral, refund requester (job failed) |
| `user` | `refund` | Requester reclaims payment after expiry |
| `user` | `view_job` | Read current job state |

**Job status codes:** 0=Open, 1=Active, 2=Settled, 3=Slashed, 4=Refunded

---

## Repo structure

```
idios_contract.h       Contract Shader header (job struct, method IDs)
idios_contract.cpp     Contract Shader (on-chain logic, escrow, settle, slash)
idios_app.cpp          App Shader (wallet-side transaction builder)
idios_payload.py       IPFS payload delivery (encrypted job specs via Beam private IPFS)
idios_job.py           Requester end-to-end script (early prototype, watch/settle logic
                       uses pre-multi-operator architecture)
idios_consensus.py     Hypertensor consensus integration (early prototype,
                       pre-multi-operator architecture)
hypertensor_trigger.py Standalone Hypertensor trigger script (early prototype,
                       pre-multi-operator architecture)
```

The three scripts marked early prototype reflect the original Hypertensor consensus integration design. They are kept in the repo as reference but are not part of the current multi operator middleware path. See [Where this is going](#where-this-is-going) for the current architecture.

---

## Quick start (dapp UI)

The simplest way to use Idios today is through the Beam Desktop wallet's dapp store.

1. Install the Beam Desktop wallet for your platform from [beam.mw](https://beam.mw)
2. Sync the wallet to mainnet and fund it with a small amount of BEAM for fees
3. Install the Idios dapp from the wallet's DApp Store (or sideload the `.dapp` file)
4. Open the Idios dapp from your installed apps

From there you can create a job, view its state, and refund it after expiry. The dapp form covers all the fields needed: job ID, subnet ID, payment amount, expiry block, node public key, and the result hash for deterministic verification.

> Note on expiry block: Beam mainnet produces a block roughly every 60 seconds. Set `expiry_block` to at least `current_block + 200` to give the create transaction time to confirm before expiry. For real jobs, `current_block + 1440` (~24 hours) is more typical.

---

## For developers and operators (CLI)

The Beam CLI wallet can drive the contract directly without going through the dapp. This is useful for building middleware, scripting, or any role beyond the requester role exposed in the dapp.

### Prerequisites

- Beam CLI wallet binary, available from the [Beam releases page](https://github.com/BeamMW/beam/releases)
- A copy of `idios_app.wasm` (downloadable from this repo or built from source)
- A Beam mainnet node to connect to (run your own, or use a public node like `eu-node01.mainnet.beam.mw:8100`)

### View a job

```bash
./beam-wallet shader \
  --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=view_job,cid=74c497b7fe906c09e0da91d1a5e43b2afe122b1a6af3ae74c9440259d6f27027,job_id=<N>" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

### Create a job (requester)

```bash
./beam-wallet shader \
  --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=create,cid=74c497b7fe906c09e0da91d1a5e43b2afe122b1a6af3ae74c9440259d6f27027,job_id=<N>,subnet_id=1,epoch=1,expiry_block=<FUTURE_BLOCK>,payment=<GROTH>,asset_id=0,node_pk=<NODE_PUBKEY>,result_hash=<HASH_HEX>" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

Payment is in groth (1 BEAM = 100,000,000 groth). For example `payment=5000000` is 0.05 BEAM.

### Commit collateral (node)

```bash
./beam-wallet shader \
  --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=commit,cid=74c497b7fe906c09e0da91d1a5e43b2afe122b1a6af3ae74c9440259d6f27027,job_id=<N>,collateral=<GROTH>,asset_id=0" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

### Refund a job (requester, after expiry)

```bash
./beam-wallet shader \
  --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=refund,cid=74c497b7fe906c09e0da91d1a5e43b2afe122b1a6af3ae74c9440259d6f27027,job_id=<N>,payment=<GROTH>,collateral=<GROTH>,asset_id=0" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

The payment, collateral, and asset_id values must match the job's actual stored values. View the job first to read them.

---

## Running an operator node

The multi operator verification network is currently in early development. The basic shape will be:

- Operators run a Beam node and CLI wallet on a machine with reasonable uptime
- A small Python script watches the contract for jobs ready for verification
- For deterministic jobs, the script automatically compares hashes and signs settle or slash decisions
- Operators coordinate via Beam's SBBS messaging layer to collect the required signatures
- Settlement requires multiple operator signatures, enforced at the contract level

If you are interested in running an operator node, open an issue on this repo or reach out directly. The first phase is recruiting a small group of operators to validate the design before opening it more broadly.

---

## Build from source

The contract and app shaders are written in C++ and built with the Beam Shader SDK.

```bash
# Rebuild contract shader
cd ~/shader-sdk/build/wasi && ninja idios_contract

# Rebuild app shader
cd ~/shader-sdk/build/wasi && ninja idios_app
```

Source files live in `~/shader-sdk/shaders/idios/`. After building, copy the resulting `.wasm` files to wherever your tooling expects them.

---

## Token dynamics

TENSOR and BEAM serve completely different functions and one strengthens the other.

TENSOR governs Hypertensor staking, node scoring, emissions, subnet participation. None of that changes with Idios. Nodes still earn TENSOR emissions through Hypertensor consensus.

What Idios adds is a separate private settlement layer for individual job payments. A requester deposits BEAM into escrow. When consensus confirms the job is done, that payment releases privately to the node. Two separate reward streams, neither cannibalising the other.

Private settlement expands the addressable market for Hypertensor subnets enterprise clients and regulated industries that can't use a subnet with public payment records become viable customers. More demand for subnet capacity means more demand for TENSOR staking.

---

## Architecture notes

**No bridge, no cross-chain protocol.** Beam and Hypertensor never communicate. The middleware is the only connection a lightweight Python process running alongside the node operator's existing stack.

**Key derivation.** The middleware key is derived from the contract ID with a different context byte than user/node keys, so it can never be confused with a user key. Both use `Env::DerivePk` in the App Shader and `Env::AddSig` in the Contract Shader native Beam multisig, not custom signatures.

**Settlement trigger design is open.** The Beam contract itself enforces the rules (escrow, expiry, multi sig). What triggers settle or slash for open ended jobs is a consensus problem that lives outside the contract. The current single operator middleware works for deterministic jobs via result hash matching. For non deterministic jobs, the design path is to build a multi operator middleware that reaches agreement among peers first, then integrate that mechanism as a Hypertensor subnet once the network is live.

**Settle/slash args are explicit.** `VarReader::Read_T` in the App Shader transaction context doesn't reliably find contract vars. Payment, collateral, and asset_id are passed explicitly and validated by the contract.

---

## Roadmap

**Phase 1: Multi operator middleware (active)**

- [ ] Multi operator keys recognised by the Beam contract
- [ ] M of N signature requirement for settle and slash
- [ ] Automated verification scripts running on operator machines
- [ ] Operator coordination via Beam SBBS messaging
- [ ] First working multi operator settlement on mainnet for a deterministic job

**Phase 2: Job delivery and operator network growth**

- [ ] Job specifications sent between requesters and nodes via Beam SBBS or compatible messaging
- [ ] IPFS payload delivery via Beam's private IPFS network for larger work files
- [ ] Encrypted payloads with keys exchanged through the messaging layer
- [ ] Recruit operators beyond the initial small group
- [ ] Operator staking and reputation tracking

**Phase 3: Verification beyond deterministic jobs**

- [ ] Verification design for non deterministic work
- [ ] Operator economics tuned from real network usage data
- [ ] Public operator directory with quality metrics

**Phase 4: Hypertensor subnet integration**

- [ ] Wrap the multi operator middleware as a Hypertensor subnet (gated on Hypertensor mainnet)
- [ ] Operators participate in Hypertensor epoch consensus and earn subnet rewards
- [ ] DHT heartbeat field for nodes to broadcast Beam pubkeys
- [ ] Migration path from standalone operator network to subnet membership

**Other directions under consideration**

- [ ] Asset support beyond BEAM (specifically Nephrite, asset_id=47)
- [ ] Additional target networks (Bittensor, Akash, others where private payment for compute is valuable)
- [ ] Native dapp support for Epoch Settlement once the trigger architecture is finalised

---

## Contributing

Idios is early and moving fast. If you're building on Hypertensor or Beam and want to integrate, open an issue or reach out directly.

Built by the community, for the community.

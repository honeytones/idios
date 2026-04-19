# Idios

**Private settlement layer for decentralised AI networks.**

Idios connects [Hypertensor](https://hypertensor.org) AI inference with [Beam MimbleWimble](https://beam.mw) private payments. When a Hypertensor subnet reaches consensus on a completed job, Idios automatically releases payment to the node , or slashes collateral if consensus fails , with no public record of amounts or identities on either chain.

> "Run AI on sensitive data , privately, verifiably, without trusting any single party."

---

## Why

Hypertensor's settlement layer is public. Anyone can see which wallets paid for AI work, which nodes were rewarded, and how much changed hands. For enterprise clients and anyone running inference on sensitive data, that's a dealbreaker regardless of how capable the AI layer is.


Idios is the community answer , specifically for settlement and payment privacy, which is achievable today without waiting for inference privacy to mature.

---

## How it works

Beam and Hypertensor never communicate directly. Python middleware running on the node operator's machine talks to both via HTTP/WebSocket.

```
Requester                 Middleware               Node
    │                         │                     │
    ├─── create job ──────────►│                     │
    │    (locks BEAM escrow)   │                     │
    │                         ├─── job via SBBS ────►│
    │                         │                     │
    │              ┌──────────┤◄── inference ───────┤
    │              │ Hypertensor epoch fires          │
    │              │ 66% attestation reached          │
    │              └──────────►                      │
    │                         │                     │
    │                         ├─── settle ──────────► Beam contract
    │                         │    (payment releases privately)
```

**Three components:**

- **Hypertensor** , AI work, consensus, node scoring
- **Idios middleware** , connects both systems (this repo)
- **Beam MimbleWimble** , private escrow, payment release, slashing

**Job lifecycle:**

1. Requester calls `create` , locks payment + specifies node pubkey and result hash
2. Node calls `commit` , locks collateral, job goes Active
3. Hypertensor epoch closes , middleware detects `RewardResult` event
4. ≥66% attestation → middleware calls `settle` → payment releases privately to node
5. <66% attestation → middleware calls `slash` → collateral burned, requester refunded

---

## Status

**Confirmed working on Beam mainnet** ✅

- Custom Contract Shader deployed (job create/commit/settle/slash/refund)
- Full job lifecycle tested end-to-end on mainnet (Job 1 settled at height ~3813939)
- Two-wallet flow proven: requester locks, node commits, middleware settles
- Python middleware wired to Beam Wallet API (invoke_contract + process_invoke_data)
- Hypertensor consensus trigger built, using subnet-template's `Hypertensor` class directly

**In progress:**

- End-to-end test with live Hypertensor node (pending mainnet launch)
- Hypertensor DHT custom field , nodes broadcast Beam pubkeys in heartbeat

---

## Contract

Deployed on Beam mainnet:

```
CID: e595078e08f00f471e7781b8e64f1d1303fa61b838f881dd646ec5f701d9251d
Deployed height: 3813751
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
idios_contract.cpp     Contract Shader (on-chain logic , escrow, settle, slash)
idios_app.cpp          App Shader (wallet-side transaction builder)
beam_settle.py         Beam Wallet API helpers
hypertensor_trigger.py Hypertensor consensus → Beam settlement trigger
```

---


## Setup Guide

This guide walks through setting up Idios from scratch on Ubuntu/Linux.

### Prerequisites

- Ubuntu 20.04+ or similar Linux distro
- Python 3.10+
- Git
- A small amount of BEAM for transaction fees

### 1. Clone Idios

    git clone https://github.com/honeytones/idios.git
    cd idios
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt

### 2. Set up Beam CLI Wallet

Create a directory for your wallet and download from the [Beam releases page](https://github.com/BeamMW/beam/releases/tag/beam-7.5.13882):

    mkdir ~/beam-wallet && cd ~/beam-wallet
    wget https://github.com/BeamMW/beam/releases/download/beam-7.5.13882/linux-beam-wallet-cli-7.5.13882.zip
    wget https://github.com/BeamMW/beam/releases/download/beam-7.5.13882/linux-wallet-api-7.5.13882.zip
    unzip linux-beam-wallet-cli-7.5.13882.zip && tar -xf beam-wallet.tar && chmod +x beam-wallet
    unzip linux-wallet-api-7.5.13882.zip && tar -xf wallet-api.tar && chmod +x wallet-api

Create a new wallet (save your seed phrase securely):

    ./beam-wallet init --node_addr=eu-node01.mainnet.beam.mw:8100

Create wallet-api.cfg in the same directory:

    pass=YOUR_PASSWORD
    wallet_path=wallet.db
    node_addr=eu-node01.mainnet.beam.mw:8100
    use_http=1
    port=10000

    chmod 600 wallet-api.cfg

### 3. Download the Idios App Shader

    wget https://github.com/honeytones/idios/raw/main/idios_app.wasm -O ~/beam-wallet/idios_app.wasm

The Idios contract is already deployed on Beam mainnet:

    CID: e595078e08f00f471e7781b8e64f1d1303fa61b838f881dd646ec5f701d9251d

### 4. Set up Hypertensor subnet-template

    git clone https://github.com/hypertensor-blockchain/subnet-template.git ~/subnet-template
    cd ~/subnet-template
    python3 -m venv venv
    source venv/bin/activate
    pip install -e .

### 5. Start the wallet-api

    cd ~/beam-wallet && ./wallet-api --enable_assets &

Verify it is running:

    curl -s -d '{"jsonrpc":"2.0","id":1,"method":"wallet_status"}' -H "Content-Type: application/json" -X POST http://127.0.0.1:10000/api/wallet

You should see your wallet balance in the response.

### 6. Get your node Beam public key

Share this with requesters so they can lock payment to your node:

    cd ~/beam-wallet && ./beam-wallet shader --shader_app_file=idios_app.wasm --shader_args="role=user,action=get_key,cid=e595078e08f00f471e7781b8e64f1d1303fa61b838f881dd646ec5f701d9251d" --node_addr=eu-node01.mainnet.beam.mw:8100

---
## Running

### Prerequisites

- Beam CLI wallet + wallet-api binary
- [hypertensor-blockchain/subnet-template](https://github.com/hypertensor-blockchain/petals_tensor)
- Python 3.10+ with `pip install -r requirements.txt`

### Start Beam wallet-api

```bash
./wallet-api --enable_assets &
```


### Create a job (requester)

```bash
./beam-wallet shader \
  --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=create,\
cid=e595078e08f00f471e7781b8e64f1d1303fa61b838f881dd646ec5f701d9251d,\
job_id=2,subnet_id=1,epoch=1,expiry_block=3900000,\
payment=10000000,asset_id=0,\
node_pk=<NODE_PUBKEY>,\
result_hash=<RESULT_HASH_HEX>" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

### Commit (node)

```bash
./beam-wallet shader \
  --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=commit,\
cid=e595078e08f00f471e7781b8e64f1d1303fa61b838f881dd646ec5f701d9251d,\
job_id=2,collateral=5000000,asset_id=0" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

### Run the trigger (middleware)

```bash
python hypertensor_trigger.py \
  --job_id 2 \
  --subnet_id 1 \
  --result_hash <RESULT_HASH_HEX> \
  --payment 10000000 \
  --collateral 5000000 \
  --mnemonic "your twelve word middleware mnemonic"
```

The trigger polls Hypertensor until the epoch closes, reads the `Network.RewardResult` event, then calls settle or slash on Beam automatically.

**Test Beam connection only:**
```bash
python hypertensor_trigger.py --job_id 2 --subnet_id 1 \
  --result_hash aabbccdd... --payment 10000000 --collateral 5000000 \
  --beam_test
```

**Test Hypertensor connection only:**
```bash
python hypertensor_trigger.py ... --mnemonic "..." --ht_test
```

### Build contract from source

Requires the [Beam Shader SDK](https://github.com/BeamMW/shader-sdk). Source files are `idios_contract.h`, `idios_contract.cpp`, and `idios_app.cpp`.

```bash
# From shader-sdk/build/wasi
ninja idios_contract && cp shaders/idios/idios_contract.wasm /path/to/idios/
ninja idios_app && cp shaders/idios/idios_app.wasm /path/to/idios/
```

---

## Token dynamics

TENSOR and BEAM serve completely different functions and one strengthens the other.

TENSOR governs Hypertensor , staking, node scoring, emissions, subnet participation. None of that changes with Idios. Nodes still earn TENSOR emissions through Hypertensor consensus.

What Idios adds is a separate private settlement layer for individual job payments. A requester deposits BEAM into escrow. When consensus confirms the job is done, that payment releases privately to the node. Two separate reward streams, neither cannibalising the other.

Private settlement expands the addressable market for Hypertensor subnets , enterprise clients and regulated industries that can't use a subnet with public payment records become viable customers. More demand for subnet capacity means more demand for TENSOR staking.

---

## Architecture notes

**No bridge, no cross-chain protocol.** Beam and Hypertensor never communicate. The middleware is the only connection , a lightweight Python process running alongside the node operator's existing stack.

**Key derivation.** The middleware key is derived from the contract ID with a different context byte than user/node keys, so it can never be confused with a user key. Both use `Env::DerivePk` in the App Shader and `Env::AddSig` in the Contract Shader , native Beam multisig, not custom signatures.

**Why `attest_data` isn't used for hash verification.** Hypertensor documents `attest_data` as "not used on-chain anywhere" , it's exchanged peer-to-peer between validators. Result hash verification is therefore the subnet's responsibility before calling the trigger. The trigger's only question to Hypertensor is: did this epoch pass 66% attestation?

**Settle/slash args are explicit.** `VarReader::Read_T` in the App Shader transaction context doesn't reliably find contract vars. Payment, collateral, and asset_id are passed explicitly and validated by the contract.

---

## Roadmap

- [ ] End-to-end test with live Hypertensor node
- [ ] Hypertensor DHT heartbeat field , nodes broadcast Beam pubkeys
- [ ] Automatic slash for dissenting individual nodes (currently slashes on epoch failure)
- [ ] Multi-node attestation (3-of-N middleware keys via Beam seamless multisig)
- [ ] IPFS job payload delivery via Beam's private IPFS network
- [ ] Nephrite (asset_id=47) payment support , fully implemented, needs testing
- [ ] Consider Bittensor / Akash as additional target networks

---

## Contributing

Idios is early and moving fast. If you're building on Hypertensor or Beam and want to integrate, open an issue or reach out directly.

Built by the community, for the community.

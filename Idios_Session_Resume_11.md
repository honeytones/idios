# IDIOS — Master Session Resume File
> Upload this file at the start of every new Claude session to resume instantly.
> Last updated: April 18 2026 — Session 11. Settle and slash fully working on Beam mainnet. All contract methods confirmed.

---

## What is Idios?
Private settlement layer for Hypertensor AI subnets using Beam MimbleWimble.
- **Hypertensor** — AI work, consensus, node scoring
- **Idios Python Middleware** — connects both systems
- **Beam MimbleWimble** — private escrow, payment release, slashing

**Tagline:** "Run AI on sensitive data — privately, verifiably, without trusting any single party."
**GitHub:** https://github.com/honeytones/idios (10 commits)

---

## Contract ID (Beam mainnet)
```
e595078e08f00f471e7781b8e64f1d1303fa61b838f881dd646ec5f701d9251d
```
Deployed height 3813751

---

## ✅ COMPLETED — Proven on Beam Mainnet

### Job 1 — Settled ✅
- Create height 3813895 — 0.1 BEAM locked
- Commit height 3813939 — 0.05 BEAM collateral
- Settle txid `9b807259492c4255b240b61a9710ad73` — status 2 ✅

### Job 7 — Slashed ✅
- Created via idios_job.py — payload encrypted + uploaded to Beam IPFS
- CID: `Qmerf9Ze1vDyJREQDpbHbyLZmcSY9ph1EUTm3Qe6uR8iXr`
- Result hash: `3d6e7d46d8d0e0373236676c264ee5b306394af60e0b57969d378bdc3ed5d71b`
- Committed — status 1 (Active), collateral 5000000 locked
- Slashed ✅ — FundsChange fix confirmed working

### Job 8 — Slashed ✅ (end-to-end via hypertensor_trigger.py)
- CID: `QmNrtVtJm9as8JjqSEgcHJf75fEorGSVxLnFLG6bxASLhF`
- Result hash: `c0ea7f0026b502e78fb5cdf98b97e69df2e482461c2345077793d8bf493ce088`
- Trigger fired at epoch 400 — attestation 0% → slash ✅

### Job 9 — Settled ✅ (direct settle confirmed)
- Same result hash as Job 8
- Committed by beam-cli2 — status 1 (Active)
- Settled directly via wallet-api middleware call — status 2 ✅

---

## Wallet Configuration — CRITICAL

**THREE wallets — know which does what:**

| Wallet | Path | Role | Pubkey (contract-derived) |
|--------|------|------|--------|
| beam-cli | `/home/tones/beam-cli/wallet.db` | **Middleware / Deployer** ✅ | `70e5f282aac08acf6c48092fa36b5f5b466fdd64715c125df717ea76e801da8500` |
| beam-cli2 | `/home/tones/beam-cli2/wallet.db` | **Node wallet** | `23f0cd450ef9225decffb4312d5f74e07afad0f59333bcd29479ed20214729da00` |
| Account1 | `/home/tones/.local/share/Beam Wallet/mainnet/Account1/7.5.13840.5763/wallet.db` | **Funding wallet** (~2230 BEAM) | `9b12931f472e7c2ebc417040a8a0576aa0866367fec5d64b2b1e49a1e220cf5600` |

⚠️ **NOTE: Session resume 10 had beam-cli marked as "empty/wrong" — this was INCORRECT. beam-cli is the deployer.**

**wallet-api is configured via cfg file — no password needed in commands:**
```bash
cd ~/beam-cli && ./wallet-api --enable_assets --enable_ipfs=true &
```

**beam-cli2 also has cfg file configured.**

**To use beam-cli2 as wallet-api (e.g. to check node key):**
```bash
pkill -f wallet-api && sleep 2 && \
cd ~/beam-cli2 && ../beam-cli/wallet-api --enable_assets --enable_ipfs=true &
```

---

## ⚠️ BEAM INTERACTIVE TRANSACTIONS — IMPORTANT

Beam uses MimbleWimble — transactions require BOTH sender and receiver wallets online simultaneously.

**Before sending BEAM between wallets:**
1. Verify receiver is listening: `ps aux | grep "beam-wallet listen" | grep -v grep`
2. Check correct wallet directory: `ls -la /proc/PID/cwd`
3. Start listener if needed: `cd ~/beam-cli2 && ./beam-wallet listen --node_addr=eu-node01.mainnet.beam.mw:8100`
4. Status "sent" = receiver not online. "sending" = in progress. "completed" = done.
5. Always confirm receiver is listening BEFORE sending.

**Wallet balances (as of Session 11):**
- beam-cli: ~11 BEAM available
- beam-cli2: ~14 BEAM available
- Account1: ~2220 BEAM available (funding wallet)

---

## What's Working

| Component | Status |
|-----------|--------|
| Beam contract — create/commit/refund | ✅ Mainnet |
| Beam contract — settle | ✅ Mainnet (Jobs 1, 9) |
| Beam contract — slash | ✅ Mainnet (Jobs 7, 8) |
| Hypertensor consensus trigger | ✅ Tested locally |
| Epoch detection + RewardResult | ✅ Working |
| DHT heartbeat beam_pubkey | ✅ Tested round-trip |
| IPFS payload delivery | ✅ Tested on Beam mainnet IPFS |
| idios_job.py create command | ✅ Working |
| idios_job.py status command | ✅ Working (key-filtered — see note below) |
| idios_job.py watch command | ✅ Working (trigger fires correctly) |

**⚠️ view_job key filtering:** wallet-api's `invoke_contract` view_job only shows jobs where the calling wallet is requester or node. Use `beam-wallet shader` CLI with beam-cli2 to reliably view job status.

---

## App Shader Fix (Session 11)

**Problem:** `On_middleware_slash` and `On_middleware_settle` passed `nullptr, 0` for FundsIO — Beam rejected because it couldn't verify fund movements.

**Fix applied:** Both functions now use `FundsChange` with `m_Consume = 0` for fund unlock:
```cpp
FundsChange fc[2];
fc[0].m_Amount = payment;    fc[0].m_Aid = asset_id; fc[0].m_Consume = 0;
fc[1].m_Amount = collateral; fc[1].m_Aid = asset_id; fc[1].m_Consume = 0;
uint32_t nFunds = (collateral > 0) ? 2 : 1;
```

**Real source file:** `~/shader-sdk/shaders/idios/app.cpp` (ninja builds from here, NOT `~/idios/idios_app.cpp`)
**After editing:** `cd ~/shader-sdk/build/wasi && rm -f shaders/idios/CMakeFiles/idios_app.dir/app.cpp.obj && ninja idios_app`
**Then copy:** `cp ~/shader-sdk/build/wasi/shaders/idios/idios_app.wasm ~/idios/`

---

## Local Dev Environment

**Hypertensor node:**
```bash
cd ~/hypertensor-v2
./target/release/solochain-template-node --dev
# ws://127.0.0.1:9944, EpochLength=10, ~6s blocks
```

**Activate venv:**
```bash
source ~/subnet-template/venv/bin/activate
export PYTHONPATH=~/subnet-template:~/subnet-template/subnet/proto
```

**Run trigger:**
```bash
PYTHONPATH=~/subnet-template:~/subnet-template/subnet/proto python3 ~/idios-repo/hypertensor_trigger.py \
  --job_id 9 --subnet_id 1 \
  --result_hash c0ea7f0026b502e78fb5cdf98b97e69df2e482461c2345077793d8bf493ce088 \
  --payment 10000000 --collateral 5000000 \
  --mnemonic "bottom drive obey lake curtain smoke basket hold race lonely fit walk" \
  --epoch 420
```

**Run idios_job.py:**
```bash
python3 ~/idios-repo/idios_job.py status --job_id 9
python3 ~/idios-repo/idios_job.py create --job_id 10 --subnet_id 1 \
  --node_beam_pk 23f0cd450ef9225decffb4312d5f74e07afad0f59333bcd29479ed20214729da00 \
  --node_rsa_pubkey ~/.idios/node_rsa_pubkey.pem \
  --payload '{"model":"llama2","prompt":"test"}' \
  --expected_result '{"output":"test result"}' \
  --payment 10000000 --expiry_block 3970000
```

---

## Build System

```bash
# Rebuild app shader
cd ~/shader-sdk/build/wasi
rm -f shaders/idios/CMakeFiles/idios_app.dir/app.cpp.obj  # force rebuild
ninja idios_app
cp ~/shader-sdk/build/wasi/shaders/idios/idios_app.wasm ~/idios/

# Rebuild contract shader
cd ~/shader-sdk/build/wasi && ninja idios_contract
cp ~/shader-sdk/build/wasi/shaders/idios/idios_contract.wasm ~/idios/

# Source files: ~/shader-sdk/shaders/idios/
# ALWAYS restart wallet-api after wasm rebuild
```

---

## Key Technical Notes

- **wallet-api auto-submits** — returns txid directly, no process_invoke_data needed
- **beam-cli is the deployer** — middleware key `70e5f282...` — cfg file configured
- **beam-cli2 is the node wallet** — node key `23f0cd...` — cfg file configured
- **Account1 is the funding wallet** — ~2230 BEAM — use for topping up test wallets
- **IPFS data format** — ipfs_add takes `list(bytes)`, ipfs_get returns list of ints → `bytes(list)`
- **RewardResult fires at** `EpochLength * (epoch + 1)`
- **No SubnetSlot in hypertensor-v2** — global epochs only
- **Error -32019** = contract Halt; **-32018** = compile error (restart wallet-api)
- **view_job is key-filtered** — use beam-wallet CLI with beam-cli2 for reliable job status
- **Settle needs result_hash match** — hash must match what was committed at job creation

---

## Job Status Codes
0=Open, 1=Active, 2=Settled, 3=Slashed, 4=Refunded

---

## Repo Files
- `idios_contract.h/cpp` — Contract Shader
- `idios_app.cpp` — App Shader ✅ slash/settle FundsChange fixed
- `hypertensor_trigger.py` — Consensus trigger
- `idios_payload.py` — IPFS payload delivery
- `idios_job.py` — Complete job flow script
- `beam_settle.py` — Old vault_anon helpers (needs porting)
- `beam_pubkey_patch.diff` — DHT heartbeat patch

---

## Conversations

**hayo (Hypertensor founder):**
- Asked if Idios needs to be a subnet → answered: private settlement system, not a subnet
- Asked about job-level verification vs epoch attestation → acknowledged gap, explained result hash bridges it
- Has not replied to last message (April 18)
- Key insight: Idios doesn't need to be a subnet now. v2 subnet = incentive mechanism for middleware operators.

**Max/Alex (Beam):**
- Confirmed async multisig (Option 3) is right for v2 multi-node middleware
- Google Doc shared for Alex: idios_beam_writeup.docx
- Beam is financially constrained — value exchange is ecosystem exposure

---

## Next Steps
1. ✅ ~~Fix slash app shader~~ — done
2. ✅ ~~Test settle end-to-end~~ — done (Job 9)
3. ✅ ~~Test slash end-to-end~~ — done (Jobs 7, 8)
4. **Reply to hayo** with job verification writeup
5. **Index hypertensor-v2 on DeepWiki** — deepwiki.com → paste GitHub URL
6. **Full end-to-end test** — pending Hypertensor mainnet
7. **Fix trigger settle path** — trigger always slashes on local dev (0% attestation). Need to either mock attestation or test on mainnet with real validators.
8. **Fix idios_job.py status** — key filtering means status always shows -1 via wallet-api. Should use beam-wallet CLI or add a middleware view action.

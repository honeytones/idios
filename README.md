# Idios

**Private escrow and settlement for AI and other compute on Beam.**

Pay for AI and compute work privately. Verifiable delivery, escrowed payment, on-chain dispute resolution. No public record of amounts or parties.

**[Website](https://honeytones.github.io/idios-site/)** · **[AI and compute use cases](https://honeytones.github.io/idios-site/private-ai-escrow.html)** · **[Latest Release](https://github.com/honeytones/idios/releases/latest)** · **[Live Explorer](https://explorer.0xmx.net/?network=mainnet&type=contract&id=41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f)**

> "Pay privately. Verify on chain. Dispute when needed."

---

## Demo

An AI agent takes a job, privately subcontracts two parts to other agents, reviews and pays each, then settles with the client, all on Beam mainnet with amounts and parties hidden the whole way.

[![asciicast](https://asciinema.org/a/3LjY52dHpejWZT0F.svg)](https://asciinema.org/a/3LjY52dHpejWZT0F)

---

## Why

Public payment rails leak. Every contract, every payment, every counterparty becomes part of a permanent searchable record. For AI inference, model training, scientific compute, work that involves proprietary inputs, private data, or competitive operations, that visibility is a dealbreaker.

Idios solves the payment and settlement privacy problem. Payment is locked in private escrow on [Beam](https://beam.mw). The worker locks collateral. Settlement happens on chain with full privacy of amounts and parties. Beam's MimbleWimble protocol hides amounts and identities at the base layer. Idios is what you get when you build escrow on that foundation.

ERC-8183 standardises the same starting primitive on Ethereum, a Job with escrowed payment for AI agent work. Idios is a separate and more complete protocol on Beam, not a port of it. It adds worker collateral and working dispute resolution, neither of which ERC-8183 has today, and it settles privately with amounts and parties hidden. Idios uses its own roles: Requester, Worker, and Arbitrator.

---

## How it works

Two settlement modes, picked per contract at creation time.

### Hash-verified Settlement (Mode A)

For deterministic work where the correct output is a specific hash known in advance.

1. Requester locks payment, declares the expected result hash.
2. Worker locks collateral.
3. Worker delivers work and submits the result hash on chain.
4. If hashes match, the contract atomically releases payment plus collateral to the worker. Done.

No arbitrator involvement. Settlement is mechanical once hashes match.

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

Idios uses a two phase claim pattern. Authorisation steps (approve, claim_after_timeout, and dispute resolution by M of N voting) set the contract status but never move funds. The beneficiary then calls `claim` to actually receive the payout, signed by their own key. This works around a Beam BVM constraint where a single kernel cannot cleanly sign for one party while routing funds to another.

---

## Status

**Live on Beam mainnet** ✅

- M of N v1 contract (Upgradable3, in place upgrades) on cid 41ef8be5. Originally deployed as v6 at block 3905992 (15 June 2026), upgraded in place to M of N v1 at block 3914637 (21 June 2026)
- M of N arbitration live: global arbitrator registry, voting based dispute resolution (N is 1 today)
- Mode A end-to-end plus all four Mode B resolution paths verified end to end with real funds
- Dapp 3.3.0 published, supports both modes via UI

**Verified resolution paths (real funds on mainnet during development; the job IDs below are from the v5 and v6 deployments that preceded the in place M of N upgrade):**

| Path | Status flow | Job |
|------|-------------|-----|
| Mode A hash match | Open → Active → Closed (single tx) | 22224 |
| Mode B approve | Open → Active → AwaitingApproval → Settled → Closed | 11112, 33335 |
| Mode B dispute, resolved to worker | Open → Active → AwaitingApproval → Disputed → ResolvedToBob → Closed | 11113 |
| Mode B dispute, resolved to requester | Open → Active → AwaitingApproval → Disputed → ResolvedToAlice → Closed | 11114 |
| Mode B timeout | Open → Active → AwaitingApproval → Settled → Closed | 11115 |
| Mode B arbitrator timeout void | Open → Active → AwaitingApproval → Disputed → Voided | 20002 |

---

## Contract

```
CID: 41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f
Current version: M of N v1 (SID 0b87c61b), upgraded in place from v6 on 21 June 2026 at block 3914637
Deployed at block: 3905992 (original v6 deploy; cid unchanged across the Upgradable3 upgrade)
Constructor params: default_review_window=10080, arbitrator_timeout_blocks=20160, upgrade_delay=1440, min_approvers=1
Explorer: https://explorer.0xmx.net/?network=mainnet&type=contract&id=41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f
```

### Roles

| Role | Description |
|------|-------------|
| `user` | Requester (Alice) or worker (Bob) interacting with the contract lifecycle |
| `arbitrator` | Member of the M of N arbitrator registry. Registers with a stake, votes on disputed contracts, and claims a reward share |
| `manager` | Deploy and view contract params (one-shot, used during contract setup) |
| `treasury` | Protocol treasury, set at deploy from the deploying wallet. Collects forfeited worker collateral from Active refunds and dispute fees from voided disputes via `sweep`. |

### Actions per role

**user actions**

| Action | Description |
|--------|-------------|
| `create_a` | Create a Mode A contract (Hash-verified Settlement). Locks payment. |
| `create_b` | Create a Mode B contract (Reviewed Settlement). Locks payment, sets review window and dispute fee. |
| `commit` | Worker locks collateral, status moves to Active. |
| `submit_delivery` | Worker submits result hash. In Mode A, pays out atomically if the hash matches and the contract moves straight to Closed. In Mode B, sets AwaitingApproval. |
| `approve` | Requester approves Mode B delivery, status moves to Settled (no funds yet). |
| `dispute` | Requester disputes Mode B delivery, locks dispute fee, status moves to Disputed. |
| `claim_after_timeout` | Worker claims after review window expires (Mode B), status moves to Settled. |
| `claim` | Beneficiary collects funds from a Settled, ResolvedToBob, or ResolvedToAlice contract. Status moves to Closed. Mode A contracts pay out automatically and cannot be claimed. |
| `refund` | Requester reclaims their payment from an expired contract (Open or Active). On the Active path the worker's collateral is forfeited to the treasury. Status moves to Refunded. |
| `void_dispute` | Permissionless. Anyone can void a dispute the arbitrator never resolved, once `arbitrator_timeout_blocks` have passed since it was filed. Status moves to Voided. |
| `void_claim_requester` | Requester reclaims their payment from a Voided contract. |
| `void_claim_node` | Worker reclaims their collateral from a Voided contract. |
| `view_job` | Read current job state. |
| `get_key` | Returns the user's pubkey for this contract. (Worker shares this with requester before create.) |

**arbitrator actions** (M of N v1)

| Action | Description |
|--------|-------------|
| `register` | Join the global arbitrator registry, posting a `stake` (sybil resistance only, not slashed in v1). Optional `arb_index` (default 0) derives a distinct key per slot. |
| `vote` | Vote on a Disputed contract. `side` 0 awards the requester (Alice), 1 awards the worker (Bob). Resolves when M matching votes land. |
| `claim_reward` | After a dispute resolves, a consensus voter claims their share of the dispute fee (`dispute_fee / M`, remainder swept to treasury). |
| `deregister` | Leave the registry. Starts the stake reclaim cooldown. |
| `reclaim` | Recover the stake after the cooldown (equal to `arbitrator_timeout_blocks`). |
| `view_arb` | Read the registry: current arbitrator count and your assigned index. |
| `get_mofn_key` | Returns the arbitrator pubkey for a given `arb_index`. |
| `get_key` | Returns the arbitrator's base pubkey for this contract. |

**treasury actions**

| Action | Description |
|--------|-------------|
| `sweep` | Collects forfeited funds: worker collateral from a Refunded contract that went through the Active path, or the dispute fee from a Voided contract. |
| `get_key` | Returns the treasury's pubkey for this contract. |

### Status codes

```
0 = Open               (just created, awaiting worker commit)
1 = Active             (worker has committed collateral)
2 = AwaitingApproval   (Mode B, worker has delivered, in review window)
3 = Disputed           (Mode B, requester has disputed, awaiting arbitrator)
4 = Settled            (beneficiary can claim, payment + collateral)
5 = Refunded           (terminal, requester reclaimed funds after expiry)
6 = ResolvedToAlice    (arbitrator sided with requester, requester can claim)
7 = ResolvedToBob      (arbitrator sided with worker, worker can claim)
8 = Closed             (terminal, claim has been collected)
9 = Voided             (terminal, arbitrator never resolved a dispute in time, parties reclaim)
```

---

## Quick start (dapp UI)

The simplest way to use Idios is through the Beam Desktop wallet's dapp store.

1. Install the Beam Desktop wallet for your platform from [beam.mw](https://beam.mw)
2. Sync to mainnet and fund with a small amount of BEAM.

    Two unrelated coins share the name and ticker. Idios runs on **Beam Mimblewimble** (privacy chain, [beam.mw](https://beam.mw), mainnet since 2019), NOT Beam Network (the Avalanche gaming subnet at onbeam.com). Make sure you buy the Mimblewimble one.

    Beam Mimblewimble is on MEXC, Gate, and CoinEx. It is not on Binance, Coinbase, or Kraken. For a quick swap from ETH to BEAM without an exchange account, [buybeam.my](https://buybeam.my/) is a community run service.
3. Install the Idios dapp from the wallet's DApp Store, or sideload the latest [`.dapp` file from releases](https://github.com/honeytones/idios/releases/latest).
4. Open Idios from your installed apps.

The dapp opens to a landing page with three entry points:

- **Start a contract**: As a requester, fill in deal terms (contract ID, payment, expiry, worker pubkey). Choose Hash-verified Settlement (upload deliverable file, dapp computes SHA-256 hash locally) or Reviewed Settlement (set review window and dispute fee). Create the contract. If you arrived via an offer link from a worker, the form auto-fills.
- **Generate a contract offer**: As a worker, fill in the agreed deal terms, upload your finished deliverable (Mode A) or set review settings (Mode B), and click Generate Offer. Produces a shareable text block and link for sending to the requester.
- **My contracts**: See live status of every contract tracked locally. Action buttons appear conditionally: Refund expired contracts, Approve or Dispute Mode B deliveries, Claim Funds when a contract is Settled or Resolved in your favour.

> Note on expiry block: Beam mainnet produces a block roughly every 60 seconds. Set `expiry_block` to at least `current_block + 200` to give the create transaction time to confirm before expiry. For real contracts, `current_block + 1440` (~24 hours) is more typical.

---

## CLI usage

The Beam CLI wallet drives the contract directly. Useful for scripting, building integrations, or any role beyond what the dapp exposes.

### Prerequisites

- Beam CLI wallet binary, available from the [Beam releases page](https://github.com/BeamMW/beam/releases)
- A copy of `idios_app.wasm` (downloadable from this repo or built from source, see [Build](#build))
- A Beam mainnet node to connect to. Run your own, or use a public node like `eu-node01.mainnet.beam.mw:8100`.

All examples use the live Idios contract on Beam Mimblewimble mainnet (`cid=41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f`) and a public node. The contract is M of N v1, upgraded in place from v6 via Upgradable3 on 21 June 2026, so the cid is unchanged and every user side call (create, commit, submit_delivery, approve, dispute, claim, refund) is byte identical to before. Substitute your own node or cid as needed.

### Get worker pubkey for this contract

The worker's pubkey is contract-specific (because the CID is part of the key derivation). Before any create, the worker runs:

```bash
./beam-wallet shader \
  --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=get_key,cid=41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

The output is the worker's `node_pk` for that contract. Send this to the requester.

### View a contract

```bash
./beam-wallet shader \
  --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=view_job,cid=41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f,job_id=<N>" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

### Create a Mode A contract (Hash-verified Settlement)

```bash
./beam-wallet shader \
  --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=create_a,cid=41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f,job_id=<N>,subnet_id=1,epoch=1,expiry_block=<FUTURE>,payment=<GROTH>,asset_id=0,node_pk=<WORKER_PUBKEY>,result_hash=<HASH>" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

Payment is in groth (1 BEAM = 100,000,000 groth). For example `payment=5000000` is 0.05 BEAM.

### Create a Mode B contract (Reviewed Settlement)

```bash
./beam-wallet shader \
  --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=create_b,cid=41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f,job_id=<N>,subnet_id=1,epoch=1,expiry_block=<FUTURE>,review_window_blocks=<N>,payment=<GROTH>,dispute_fee=<GROTH>,asset_id=0,node_pk=<WORKER_PUBKEY>" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

### Commit collateral (worker)

```bash
./beam-wallet shader \
  --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=commit,cid=41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f,job_id=<N>,collateral=<GROTH>,asset_id=0" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

### Submit delivery (worker)

```bash
./beam-wallet shader \
  --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=submit_delivery,cid=41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f,job_id=<N>,delivery_hash=<HASH>,mode=<65|66>,payment=<GROTH>,collateral=<GROTH>,asset_id=0" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

`mode=65` for Mode A (ASCII 'A'), `mode=66` for Mode B (ASCII 'B').

### Mode B: approve, dispute, claim_after_timeout

```bash
# Requester approves
./beam-wallet shader --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=approve,cid=41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f,job_id=<N>" \
  --node_addr=eu-node01.mainnet.beam.mw:8100

# Requester disputes
./beam-wallet shader --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=dispute,cid=41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f,job_id=<N>,dispute_fee=<GROTH>,asset_id=0" \
  --node_addr=eu-node01.mainnet.beam.mw:8100

# Worker claims after review window expires
./beam-wallet shader --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=claim_after_timeout,cid=41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f,job_id=<N>" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

### Arbitrator: register, vote, and claim reward (M of N v1)

Disputes resolve by M of N voting. Arbitrators join a global registry with a standing stake, vote on disputed contracts, and claim a reward share once consensus is reached. The stake is sybil resistance only and is not slashed in v1. `arb_index` is optional on every arbitrator call and defaults to 0; each index derives a distinct key, so one wallet can run several indices. Stake is in groth (1 BEAM = 100,000,000 groth); `stake=1000000` is 0.01 BEAM, the live registration amount on production today.

```bash
# Register as an arbitrator (one time, posts the stake)
./beam-wallet shader --shader_app_file=idios_app.wasm \
  --shader_args="role=arbitrator,action=register,cid=41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f,stake=1000000,asset_id=0" \
  --node_addr=eu-node01.mainnet.beam.mw:8100

# View the registry (current arbitrator count and your index)
./beam-wallet shader --shader_app_file=idios_app.wasm \
  --shader_args="role=arbitrator,action=view_arb,cid=41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f" \
  --node_addr=eu-node01.mainnet.beam.mw:8100

# Vote on a dispute. side=0 awards the requester (Alice), side=1 awards the worker (Bob).
# The contract freezes N (registry size at filing) and M (= N/2 + 1); only arbitrators
# registered at or before the filing block may vote. Resolves when M matching votes land.
./beam-wallet shader --shader_app_file=idios_app.wasm \
  --shader_args="role=arbitrator,action=vote,cid=41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f,job_id=<N>,side=<0|1>" \
  --node_addr=eu-node01.mainnet.beam.mw:8100

# Claim your reward share after the dispute resolves (consensus voters only; dispute_fee / M each)
./beam-wallet shader --shader_app_file=idios_app.wasm \
  --shader_args="role=arbitrator,action=claim_reward,cid=41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f,job_id=<N>" \
  --node_addr=eu-node01.mainnet.beam.mw:8100

# Deregister, then reclaim the stake after the cooldown (= arbitrator_timeout_blocks)
./beam-wallet shader --shader_app_file=idios_app.wasm \
  --shader_args="role=arbitrator,action=deregister,cid=41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f" \
  --node_addr=eu-node01.mainnet.beam.mw:8100

./beam-wallet shader --shader_app_file=idios_app.wasm \
  --shader_args="role=arbitrator,action=reclaim,cid=41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

Each arbitrator slot has a distinct pubkey, derived via `MofnArbKeyID { tag 'N', ctx 1, m_Idx }`. Run `action=get_mofn_key` with the relevant `arb_index` to read yours.

### Claim funds (beneficiary)

After a contract reaches Settled, ResolvedToAlice, or ResolvedToBob, the beneficiary calls `claim`. The contract reads payment and collateral from the job state, so no payout amount is passed.

```bash
./beam-wallet shader --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=claim,cid=41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f,job_id=<N>,asset_id=0" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

The beneficiary receives `payment + collateral` in all three cases. In M of N v1 the dispute fee does not go to the winner of a dispute; it is split across the consensus voters via `claim_reward` (see the arbitrator section), with any remainder swept to treasury.

### Refund (requester, after expiry)

For an Open contract whose `expiry_block` has passed without anyone committing.

```bash
./beam-wallet shader --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=refund,cid=41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f,job_id=<N>,payment=<GROTH>,collateral=<GROTH>,asset_id=0" \
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

**Two phase claim.** Authorisation steps (approve, claim_after_timeout, and dispute resolution by M of N voting) set the contract status. The beneficiary then calls Method_15 Claim signed with their own key to actually receive the funds. This works around a Beam BVM constraint where one kernel can't cleanly sign for one party while routing funds to a different party.

**Arbitrator contact.** To reach the arbitrator for dispute resolution, message **@tappyoak** on Telegram. Include the contract ID, your role, and a brief description of the dispute.

**Talking to the other party.** Every party in a contract already runs a Beam wallet, and the wallet has private messaging built in, so requester, worker, and arbitrator can coordinate directly with no outside app. Open it from the account menu at the top right, then Beam Messenger. To start a chat click New chat, paste the other party's Beam messaging address into the Address field, give them a name, and add them. Type your message and press ctrl+enter, command+enter on a Mac, or click Send on the right, to send. Your own messaging address, the one you hand out so others can reach you, is the My address shown in that same New chat dialog. Messages travel wallet to wallet over Beam's SBBS layer and are never posted on chain. Dispute resolution still goes through the arbitrator above, this is for coordinating with your counterparty.

**M of N arbitration (live).** The contract holds a global arbitrator registry, each slot backed by a standing stake. Filing a dispute freezes N (the registry size at filing time) and M (= N/2 + 1) into that dispute, and only arbitrators registered at or before the filing block may vote. The contract resolves when M matching votes land. Consensus voters split the dispute fee (`dispute_fee / M` each, remainder to treasury); arbitrators never receive the winning party's funds. The stake is sybil resistance only and is not slashed in v1.

Today N is 1, a single registered arbitrator (contact below). Registering more independent arbitrators is the immediate next step and is open to anyone willing to post the stake. The dapp, MCP server, and agent daemon still carry the older single arbitrator console; since the escrow flow is byte identical across the upgrade nothing there breaks, and the arbitrator surface rework (register, vote, claim_reward via UI and MCP) is deferred until external arbitrators need it. The CLI path above works today.

**Contract-specific keys.** Every party derives their pubkey using `Env::DerivePk` with the contract ID as part of the input. A worker's pubkey on contract A is different from their pubkey on contract B. Always run `get_key` on the target contract before passing `node_pk` into create.

**Refund semantics.** `refund` returns the requester's payment from an expired contract once `expiry_block` has passed, and it works in two cases. If no worker ever committed (status Open), the payment is simply returned. If a worker committed but never delivered (status Active), the requester still gets only the payment back and the worker's collateral is forfeited to the treasury, not returned to either side. This penalises a worker who locked in and then went silent, and it stops a requester from setting a tight expiry to grab the worker's stake, since the stake always goes to the treasury and never to the requester. A job that has been delivered cannot be refunded, it must complete a resolution path.

**Void semantics.** If a dispute is filed and the arbitrator never resolves it within `arbitrator_timeout_blocks`, anyone can call `void_dispute` after that window to move the contract to Voided. The requester then reclaims their payment with `void_claim_requester`, the worker reclaims their collateral with `void_claim_node`, and the treasury sweeps the dispute fee. This guarantees funds can never be trapped by an absent arbitrator.

**No bridge, no cross chain.** Idios runs entirely on Beam mainnet. No wrapped assets, no second chain.

---

## Agent runtime daemon

`idios-agent-daemon/` in this repo is a small Python daemon that automates your role in an Idios contract. Polls the chain, watches your tracked contracts, fires the right contract call when the state machine advances. Run on your own machine alongside a Beam CLI wallet, type the password once at startup, walk away.

Supports all three Idios roles:

- **Worker** commit, submit_delivery, claim.
- **Client** hash-match auto-approve, claim on dispute won or refund.
- **Arbitrator**: hash-match auto-vote in Mode B disputes (vote for the worker on a match, the requester on a mismatch). This daemon path is being migrated from the retired single-arbitrator resolve to M of N voting; the worker and client roles are unaffected.

The daemon automates all three roles and has been exercised end to end on Beam mainnet, including the dispute and arbitrator timeout recovery paths.

The dapp's My Contracts page has an **Automate this contract** button on each tracked contract card that generates a daemon config snippet you can paste straight into the daemon's config. See [`idios-agent-daemon/README.md`](./idios-agent-daemon/README.md) for setup and configuration.

---

## Roadmap

This roadmap is partner-driven. Phase 0 is live on mainnet. Phase 1 is the next planned release. Everything after depends on real-world demand.

**Phase 0 (shipped): live on mainnet**

- [x] M of N v1 contract on Beam mainnet (cid `41ef8be5...`), upgraded in place from v6 via Upgradable3 on 21 June 2026: global arbitrator registry, voting based dispute resolution, stake based sybil resistance
- [x] Both Hash verified (Mode A) and Reviewed (Mode B) settlement
- [x] Four Mode B resolution paths verified end to end on mainnet: approve, dispute to either side, review timeout, arbitrator timeout void
- [x] Worker collateral floor, mutual cancel, and review window fallback (the v5 surgical set)
- [x] Dapp 3.3.0 (still on the v6 escrow surface, byte identical so unaffected; arbitrator surface rework deferred)
- [x] BEAM and Nephrite (NPH) settlement, both tested on mainnet, plus any other Beam confidential asset by `asset_id`
- [x] Explorer parser, agent runtime daemon (worker, requester, arbitrator roles), and in-dapp daemon config export

**Phase 1 (next): real decentralization and reputation**

- [ ] Register more arbitrators. N is 1 today; adding two more independently held keys, run by separate operators, gives a real 2 of 3 quorum. Open to anyone willing to post the stake. CLI works now, surface rework comes later
- [ ] Arbitrator surface rework. Dapp arbitrator console, MCP server arbitrator tools, and agent daemon arbitrator role rewired from the retired resolve methods to register, vote, and claim_reward. Deferred until external arbitrators need a UI; CLI is the path today
- [ ] Arbitrator slashing. The stake in v1 is sybil resistance only. Slashing arbitrators who vote against consensus is the next contract upgrade, in place via Upgradable3
- [ ] Privacy preserving reputation. Worker bond and slash as an in place upgrade, an off chain score reader, and a paid handle via Beam NameService so reputation attaches to a public handle without leaking transaction history

**Phase 2: payload delivery and larger deliverables**

- [ ] Larger work artifacts. Today the deliverable is identified by a 32 byte hash and the content is exchanged out of band. The next step uses Beam Desktop's integrated IPFS layer so workers can publish encrypted deliverables and requesters can fetch them, while the contract still only commits to the hash
- [ ] Encrypted payloads, keys exchanged out of band. The on chain hash stays the binding commitment; what changes is that the payload gets a canonical place to live
- [ ] Larger work files supported beyond what fits in a single hand off

**Phase 3+: future research (partner driven)**

- [ ] Programmatic verifiers for narrow job classes (e.g. JSON schema match, deterministic output equivalence) where a partner has a concrete need
- [ ] Decentralized arbitrator network with emissions and slashing, partner driven. Idios stays a Beam contract; an external incentive layer would handle staking and slashing of operators
- [ ] Cross chain settlement only if a real partner forces it. Default position: stay on Beam mainnet, do not bridge

---

## Contributing

Idios is early and moving fast. If you're building on Beam and want to integrate, open an issue or reach out directly.

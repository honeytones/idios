# Idios Quickstart: run an agent on private escrow

Idios is private escrow on Beam. Your agent creates a work contract, locks payment, the worker locks collateral, work is delivered, and funds settle privately. Amounts and parties are hidden at the base layer. This guide takes you from nothing to a settled contract driven by an agent.

It assumes you are a developer running an agent on a server or a dev box (Linux or Mac). No GUI required anywhere.

## Heads up: there are two coins called Beam

You want the privacy coin built on MimbleWimble called Beam privacy, from **beam.mw**. There is a completely different project, Beam Network, a gaming token on Avalanche at onbeam.com, which has nothing to do with Beam Privacy. Everything below is the MimbleWimble Beam.

## What you need

- Python 3.10 or newer
- The Beam CLI wallet binary (comes with the Beam wallet download from beam.mw)
- Some BEAM, for transaction fees and, in a test, as the payment and collateral asset itself (a whole test contract costs only cents)
- An MCP capable agent client (Claude Code on Linux, or any framework with MCP support: LangGraph, CrewAI, AutoGen)

## 1. Get and create a wallet

Download the Beam wallet from beam.mw and use the CLI binary that comes with it. Create a wallet:

    ./beam-wallet init

Set a password and save the seed phrase. This gives you the binary plus a wallet.db. The wallet holds your keys and funds and signs every action, so it stays on your own machine. Never put it on a server you do not control.

## 2. Fund the wallet

For testing, all you need is a little BEAM. The easiest way is buybeam.my, a community run swap that takes ETH straight to BEAM and sends it to your Beam wallet address in seconds, no exchange account needed. You can also buy BEAM on a centralised exchange like MEXC, Gate, or CoinEx using USDT, BTC or ETH and withdraw it.

A whole test contract costs only cents, and in a self dealing test, where you play both sides, the funds cycle back to you. Use BEAM (asset id 0) as the payment and collateral asset and you are ready.

NPH (asset id 47) is optional. It is a USD pegged confidential stablecoin on Beam, worth using when you want to hold value steadily over time rather than sit in volatile BEAM, so more for real payments than quick tests. You get it by swapping BEAM to NPH on the DEX inside the Beam Desktop wallet.


## 3. Point at a node

The wallet talks to the network through a Beam node. You do not need to run the Desktop GUI.

- Simple: use a public mainnet node, for example `eu-node01.mainnet.beam.mw:8100`. Your keys stay local, the node just relays. The tradeoff is you depend on that node being up.
- Production: run your own headless beam node, sync it against mainnet, and point the wallet at your own localhost node. More robust and fully self hosted. For anything serious, do this, or at least keep a couple of public nodes as fallbacks.

## 4. Get the Idios shader

Download `idios_app.wasm` from github.com/honeytones/idios. The wallet runs this to talk to the contract. Note its path on disk.

## 5. Set up the MCP server

Grab the `idios-mcp-server` folder from the same repo. Create a virtualenv and install the MCP SDK into it:

    python3 -m venv ~/idios-mcp-venv
    ~/idios-mcp-venv/bin/pip install mcp

Copy `idios_mcp_config.example.json` to `idios_mcp_config.json` and fill in five fields:

    {
      "beam_wallet_binary": "/path/to/beam-wallet",
      "shader_app_file": "/path/to/idios_app.wasm",
      "wallet_path": "/path/to/wallet.db",
      "node_addr": "eu-node01.mainnet.beam.mw:8100",
      "cid": "41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f"
    }

The cid is the live Idios contract on Beam mainnet.

## 6. Wire it into your agent

Pass your wallet password through the `IDIOS_WALLET_PASS` environment variable so the server starts without a prompt. Export it in the shell before launching your agent, and the server inherits it.

Claude Code (the path on Linux):

    read -s -p "Wallet password: " IDIOS_WALLET_PASS && export IDIOS_WALLET_PASS && echo
    claude mcp add --scope user idios -- ~/idios-mcp-venv/bin/python /path/to/idios_mcp_server.py --config /path/to/idios_mcp_config.json
    claude

For LangGraph, CrewAI, AutoGen: use their MCP client config with the same command, args and the `IDIOS_WALLET_PASS` env entry. The server speaks stdio, which they all support.

## 7. Run it

Your agent now has the Idios tools: get_chain_info, view_contract, get_key, create_contract_a, create_contract_b, commit_collateral, submit_delivery, approve_delivery, dispute_delivery, view_dispute, claim_funds, claim_after_timeout, refund_contract, mutual_cancel, void_dispute, void_claim_requester, void_claim_node, worker_register, worker_deregister, worker_reclaim, view_worker_bond, treasury_sweep.

Prove it end to end with a self dealing test, where you are both sides. Ask the agent something like:

    Run a full test contract: create a Mode B contract with the worker pubkey set to my own wallet, a small payment in BEAM, a short expiry. Then commit collateral, submit a delivery, approve it, and claim the funds. Tell me the final state.

It should walk the contract from Open to Closed and return your test funds. That confirms the whole stack: wallet, node, MCP server, and the agent driving it.

## Disputes

If a delivery is contested, the agent can file a dispute but cannot resolve it. Resolution is handled by the M of N arbitrator registry through voting, separate from the parties, so an agent can never rule in its own favour. One arbitrator is registered today (N is 1). Contact the arbitrator at @tappyoak on Telegram or Discord with the contract id and your side of it.

The dispute winner receives the payment plus the worker's collateral. The dispute fee is not awarded to either side: it pays the voting arbitrators. After resolution the winner claims with claim_funds; the contract stays at its Resolved status forever, and the winner_paid flag in view_dispute is the signal the payout happened.

## Worker reputation bond

Workers can post a slashable BEAM bond with worker_register. It is tied to your worker pubkey, and a requester can check it with view_worker_bond before hiring you: live stake means losing a dispute costs you the bond (it gets slashed and collected by the treasury). Withdraw with worker_deregister, then worker_reclaim after a cooldown equal to the contract's arbitrator timeout. Reclaim waits while any open dispute encumbers the bond, and a slashed bond is gone for good.

Parties can also coordinate privately over Beam Messenger, built into the Beam wallet under the account menu. Exchange your Beam messaging addresses, add the other party under New chat, and message wallet to wallet over SBBS, nothing on chain. The arbitrator at @tappyoak stays the resolution contact.

## The two modes

- Mode A, hash verified: the buyer locks an expected result hash at creation. When the worker submits a matching hash the contract auto settles and pays out, no review. Best for deterministic deliverables: a dataset, a model file, a known output.
- Mode B, reviewed: the buyer reviews the delivery and approves or disputes, with the worker's collateral at risk and an arbitrator as backstop. Best for judgement work: custom builds, analysis, anything where a human or agent has to assess the result.

## Amount units

All amounts are in groth. 1 BEAM = 100,000,000 groth, and NPH uses the same unit. So 0.01 NPH = 1,000,000 groth.

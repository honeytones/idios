# Idios MCP Server

> New here? Start with [QUICKSTART.md](QUICKSTART.md): from nothing to a settled contract, for an agent developer.

Exposes Idios private escrow contract actions as MCP tools so any MCP-compatible
AI agent framework (LangGraph, CrewAI, AutoGen, Claude, or any framework with MCP
support) can create and manage private work contracts on Beam without human involvement.

## What this enables

An AI agent can:

- Create a private escrow contract and lock payment
- Commit collateral as a worker before delivering
- Submit delivery and auto-settle on hash match (Mode A)
- Approve or dispute a delivery (Mode B)
- Claim funds after settlement or dispute resolution
- View current contract state at any time

All settlement is private on Beam MimbleWimble. Amounts and parties are hidden
at the protocol level. No platform takes a cut.

## Prerequisites

- Python 3.10+
- Beam CLI wallet binary on disk
- Idios app shader (idios_app.wasm) on disk
- A funded wallet.db
- Node 22+ (for Wrangler if needed, otherwise just Python)

Install the MCP SDK:

    pip install mcp

## Config

Copy idios_mcp_config.example.json to idios_mcp_config.json and fill in your paths:

    {
      "beam_wallet_binary": "/home/you/beam-cli/beam-wallet",
      "shader_app_file": "/path/to/idios_app.wasm",
      "wallet_path": "/home/you/beam-cli/wallet.db",
      "node_addr": "127.0.0.1:10005",
      "cid": "ed788e2f03faf0a461d110725509aa49b93671007bb554ea4baea077236ac3cb"
    }

node_addr of 127.0.0.1:10005 is the embedded node inside Beam Desktop wallet.
If Beam Desktop is not running, use a public mainnet node like eu-node01.mainnet.beam.mw:8100.

## Running

    python3 idios_mcp_server.py --config idios_mcp_config.json

The server needs your wallet password. Run by hand, it prompts once at
startup. When an MCP client spawns it over stdio, set the password in the
IDIOS_WALLET_PASS environment variable so it starts without a prompt; the
server falls back to the prompt only when that variable is unset. The
password is held in memory and never written to disk by the server.

Install the mcp package into a virtualenv and run the server with that
venv python (for example /path/to/venv/bin/python idios_mcp_server.py ...)
so the client spawns it with the mcp package available.

## Connecting to your agent framework

The server uses stdio transport, which every major MCP client supports
(Claude Code, Claude Desktop, LangGraph, CrewAI, AutoGen, and others).

### Claude Code (the path on Linux, where there is no Claude Desktop)

Export the password in the shell, then register the server. Keeping the
password out of the command and config means it is never written to disk:

    read -s -p "Wallet password: " IDIOS_WALLET_PASS && export IDIOS_WALLET_PASS && echo
    claude mcp add --scope user idios -- /path/to/venv/bin/python /path/to/idios_mcp_server.py --config /path/to/idios_mcp_config.json

The server inherits IDIOS_WALLET_PASS from the shell Claude Code runs in, so
launch claude from that same shell. Then ask in plain language, for example
"use idios to view contract 99903" or "create a Mode B contract and settle it".

### Config-file clients (Claude Desktop and others)

    {
      "mcpServers": {
        "idios": {
          "command": "/path/to/venv/bin/python",
          "args": ["/path/to/idios_mcp_server.py", "--config", "/path/to/idios_mcp_config.json"],
          "env": { "IDIOS_WALLET_PASS": "your-wallet-password" }
        }
      }
    }

Putting the password in the env block writes it to that client config file in
plaintext. Fine on a machine you control, but know the tradeoff.

## Available tools

| Tool | Role | What it does |
|---|---|---|
| view_contract | any | Read current contract state from chain |
| get_chain_info | any | Read current block height, to pick a future expiry_block |
| get_key | any | Get your own pubkey, the value a counterparty uses as worker_pubkey |
| create_contract_b | requester | Create Mode B (reviewed) contract, locks payment |
| create_contract_a | requester | Create Mode A (hash-verified) contract, locks payment + result hash |
| commit_collateral | worker | Lock collateral to activate contract |
| submit_delivery | worker | Submit delivery hash, auto-settles Mode A on match |
| approve_delivery | requester | Approve Mode B delivery, worker can then claim |
| dispute_delivery | requester | Dispute Mode B delivery, routes to arbitrator |
| claim_funds | either | Claim from a Settled or Resolved (dispute) contract |
| claim_after_timeout | worker | Claim after requester goes silent past review window |
| refund_contract | requester | Refund expired Open contract (worker never committed) |

## Amount units

All amounts are in groth. 1 BEAM = 100,000,000 groth. NPH (asset_id=47) uses the
same unit. So 5 NPH = 500,000,000 groth, 0.05 BEAM = 5,000,000 groth.

## Timing

view_contract is fast (read-only). State-changing calls (commit_collateral,
submit_delivery, approve_delivery, dispute_delivery, claim_funds) wait for
on-chain confirmation and may take 1-2 minutes on Beam mainnet.

## Arbitrator

For disputes, contact @tappyoak on Telegram or Discord with the contract ID,
your role, and a description of the situation.

## Live contract

CID: ed788e2f03faf0a461d110725509aa49b93671007bb554ea4baea077236ac3cb
Deployed on Beam mainnet since May 2, 2026.

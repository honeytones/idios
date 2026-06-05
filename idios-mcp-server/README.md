# Idios MCP Server

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
      "cid": "f40eb64da63a69d91afa1a947d9d272a9f80027d7261aa822ec0e4b5827cdc45"
    }

node_addr of 127.0.0.1:10005 is the embedded node inside Beam Desktop wallet.
If Beam Desktop is not running, use a public mainnet node like eu-node01.mainnet.beam.mw:8100.

## Running

    python3 idios_mcp_server.py --config idios_mcp_config.json

The server prompts for your wallet password once at startup, then serves via
stdio transport. Password is held in memory only, never written to disk.

## Connecting to your agent framework

Add the server to your agent's MCP config. Example for Claude Desktop:

    {
      "mcpServers": {
        "idios": {
          "command": "python3",
          "args": ["/path/to/idios_mcp_server.py", "--config", "/path/to/idios_mcp_config.json"]
        }
      }
    }

For LangGraph, CrewAI, AutoGen: use their respective MCP client configuration.
The server uses stdio transport which all major frameworks support.

## Available tools

| Tool | Role | What it does |
|---|---|---|
| view_contract | any | Read current contract state from chain |
| create_contract_b | requester | Create Mode B (reviewed) contract, locks payment |
| create_contract_a | requester | Create Mode A (hash-verified) contract, locks payment + result hash |
| commit_collateral | worker | Lock collateral to activate contract |
| submit_delivery | worker | Submit delivery hash, auto-settles Mode A on match |
| approve_delivery | requester | Approve Mode B delivery, worker can then claim |
| dispute_delivery | requester | Dispute Mode B delivery, routes to arbitrator |
| claim_funds | either | Claim from Settled, Resolved, or Refunded contract |
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

CID: f40eb64da63a69d91afa1a947d9d272a9f80027d7261aa822ec0e4b5827cdc45
Deployed on Beam mainnet since May 2, 2026.

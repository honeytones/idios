#!/usr/bin/env python3
"""
Idios MCP Server

Exposes Idios contract actions as MCP tools so any MCP-compatible AI agent
framework (LangGraph, CrewAI, AutoGen, Claude, etc.) can create and manage
private escrow contracts on Beam without human involvement.

Each tool maps directly to one Idios contract action. The server runs locally
alongside a Beam CLI wallet. Password is prompted once at startup.

Setup:
    pip install mcp
    python3 idios_mcp_server.py --config /path/to/idios_mcp_config.json

Config format (idios_mcp_config.json):
    {
      "beam_wallet_binary": "/home/you/beam-cli/beam-wallet",
      "shader_app_file": "/path/to/idios_app.wasm",
      "wallet_path": "/home/you/beam-cli/wallet.db",
      "node_addr": "127.0.0.1:10005",
      "cid": "f40eb64da63a69d91afa1a947d9d272a9f80027d7261aa822ec0e4b5827cdc45"
    }

The server uses stdio transport, meaning the agent framework starts it as a
subprocess and communicates via stdin/stdout. This is the standard local MCP
pattern and keeps the wallet password off the network.

Notes:
    - beam-wallet shader exits rc=1 even on success. Trust parsed output not rc.
    - State-changing calls (commit, submit_delivery, approve, dispute, claim)
      can take 1-2 minutes to confirm on chain. SHADER_TIMEOUT_SECONDS=600.
    - view_contract is read-only and fast.
    - The CID in config must match the deployed Idios contract.
    - expiry_block must be in the future. Use current_block + 50000 for ~7 days.
    - Amounts are in groth. 1 BEAM = 100,000,000 groth. NPH (asset_id=47) same.
"""

import sys
import os
import json
import getpass
import logging
import subprocess
import argparse
from typing import Optional

# Suppress mcp library logging to keep stdio clean.
logging.getLogger("mcp").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("ERROR: mcp package not installed. Run: pip install mcp", file=sys.stderr)
    sys.exit(1)

SHADER_TIMEOUT_SECONDS = 600

STATUS_NAMES = {
    0: "Open",
    1: "Active",
    2: "AwaitingApproval",
    3: "Disputed",
    4: "Settled",
    5: "Refunded",
    6: "ResolvedToAlice",
    7: "ResolvedToBob",
    8: "Closed",
}

# Global config and password set at startup before serving.
_cfg: dict = {}
_password: str = ""


def _parse_shader_output(stdout_text: str) -> Optional[dict]:
    """Parse Shader output: line from beam-wallet stdout."""
    for line in stdout_text.splitlines():
        idx = line.find("Shader output:")
        if idx == -1:
            continue
        raw = line[idx + len("Shader output:"):].strip()
        if not raw:
            return None
        try:
            return json.loads("{" + raw + "}")
        except json.JSONDecodeError:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"_raw": raw, "_parse_error": True}
    return None


def _call_shader(shader_args: str) -> tuple:
    """
    Run beam-wallet shader. Returns (ok, parsed_output, error_message).
    beam-wallet exits rc=1 even on success. Trust parsed output not rc.
    """
    cmd = [
        _cfg["beam_wallet_binary"],
        "shader",
        "--pass=" + _password,
        "--shader_app_file=" + _cfg["shader_app_file"],
        "--shader_args=" + shader_args,
        "--node_addr=" + _cfg["node_addr"],
        "--wallet_path=" + _cfg["wallet_path"],
    ]
    wallet_cwd = os.path.dirname(_cfg["beam_wallet_binary"])
    try:
        proc = subprocess.run(
            cmd,
            input=b"y\n",
            capture_output=True,
            timeout=SHADER_TIMEOUT_SECONDS,
            cwd=wallet_cwd,
        )
    except subprocess.TimeoutExpired:
        return False, None, "Shader call timed out after {}s".format(SHADER_TIMEOUT_SECONDS)

    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    parsed = _parse_shader_output(stdout)

    if parsed is not None:
        return True, parsed, None

    # State-changing calls (create, commit, submit, approve, dispute, claim,
    # refund) emit no parseable "Shader output:" object, only node sync logs
    # and a completion line. beam-wallet also exits rc=1 on success, so the
    # reliable success signal here is the transaction completion marker.
    if "Transaction completed" in stdout:
        return True, None, None

    err = stderr[-400:].strip() if stderr else stdout[-400:].strip()
    return False, None, "Shader call failed. " + err


def _build_args(parts: list) -> str:
    """Build shader_args string with cid prefix."""
    pairs = ["cid=" + _cfg["cid"]]
    for k, v in parts:
        pairs.append("{}={}".format(k, v))
    return ",".join(pairs)


def _status_name(status_int) -> str:
    try:
        return STATUS_NAMES.get(int(status_int), "Unknown({})".format(status_int))
    except Exception:
        return "Unknown({})".format(status_int)


# Initialise FastMCP server.
mcp = FastMCP(
    "idios",
    instructions=(
        "Idios is a private escrow protocol on Beam MimbleWimble. "
        "Use these tools to create and manage private work contracts. "
        "Both sides lock funds before work starts. Amounts and parties stay private. "
        "All amounts are in groth (1 BEAM = 100,000,000 groth, NPH asset_id=47 same unit). "
        "expiry_block must be in the future: use current block + 50000 for roughly 7 days. "
        "State-changing calls (commit, submit_delivery, approve, dispute, claim) "
        "wait for on-chain confirmation and may take 1-2 minutes."
    )
)


@mcp.tool()
def view_contract(job_id: int) -> str:
    """
    Get the current on-chain state of an Idios contract.

    Returns all contract fields including status, payment, collateral,
    dispute_fee, delivery_hash, expiry_block, mode, and asset_id.

    Status values: Open(0), Active(1), AwaitingApproval(2), Disputed(3),
    Settled(4), Refunded(5), ResolvedToAlice(6), ResolvedToBob(7), Closed(8).

    Use this to check contract state before deciding what action to take.
    This call is read-only and does not require wallet funds.
    """
    args = "role=manager,action=view_job," + _build_args([("job_id", job_id)])
    ok, parsed, err = _call_shader(args)
    if not ok or parsed is None:
        return "Error viewing contract {}: {}".format(job_id, err)
    job = parsed.get("job") or parsed
    status_int = job.get("status", -1)
    job["status_name"] = _status_name(status_int)
    return json.dumps(job, indent=2)


@mcp.tool()
def create_contract_b(
    job_id: int,
    worker_pubkey: str,
    payment: int,
    asset_id: int,
    expiry_block: int,
    review_window_blocks: int,
    dispute_fee: int,
    subnet_id: int = 1,
    epoch: int = 1,
) -> str:
    """
    Create a Mode B (Reviewed settlement) Idios escrow contract.

    Locks payment from your wallet into escrow. The worker must then call
    commit_collateral to begin work. After delivery, you review and approve
    or dispute. If you do nothing within review_window_blocks, the worker
    can claim via claim_after_timeout.

    Use Mode B for any work where a human or agent needs to judge the output:
    custom AI agents, automation pipelines, consulting, data labelling,
    model fine-tuning where exact output hash is not known in advance.

    Args:
        job_id: Unique integer ID you choose. Must not already exist on chain.
        worker_pubkey: Worker's Beam pubkey from their Idios dapp or get_key action.
        payment: Payment amount in groth (1 BEAM = 100,000,000 groth).
        asset_id: 0 for BEAM, 47 for NPH (USD-pegged stablecoin).
        expiry_block: Block height when contract expires. Use current_block + 50000 for ~7 days.
        review_window_blocks: How long requester has to approve/dispute after delivery.
            2000 blocks is roughly 33 hours.
        dispute_fee: Amount requester locks if they dispute. Lost if dispute goes against them.
        subnet_id: Subnet identifier (default 1).
        epoch: Epoch (default 1).

    Returns confirmation once contract is on chain, or error message.
    """
    args = "role=user,action=create_b," + _build_args([
        ("job_id", job_id),
        ("subnet_id", subnet_id),
        ("epoch", epoch),
        ("expiry_block", expiry_block),
        ("review_window_blocks", review_window_blocks),
        ("payment", payment),
        ("dispute_fee", dispute_fee),
        ("asset_id", asset_id),
        ("node_pk", worker_pubkey),
    ])
    ok, parsed, err = _call_shader(args)
    if not ok:
        return "Error creating contract {}: {}".format(job_id, err)
    return "Contract {} created (Mode B). Payment {} groth locked. Worker must now commit collateral.".format(
        job_id, payment
    )


@mcp.tool()
def create_contract_a(
    job_id: int,
    worker_pubkey: str,
    payment: int,
    asset_id: int,
    expiry_block: int,
    result_hash: str,
    subnet_id: int = 1,
    epoch: int = 1,
) -> str:
    """
    Create a Mode A (Hash-verified settlement) Idios escrow contract.

    Locks payment from your wallet. The expected output hash is locked in
    at creation time. When the worker submits the same hash, the contract
    auto-settles and releases payment. No human approval needed.

    Use Mode A for deterministic outputs where the exact deliverable is
    agreed before the contract starts: a specific model file, dataset,
    or any output with a known SHA-256 hash.

    Both parties must agree on result_hash before the contract is created.
    The worker should send you the hash of their deliverable in advance
    for you to verify before locking it in.

    Args:
        job_id: Unique integer ID you choose. Must not already exist on chain.
        worker_pubkey: Worker's Beam pubkey from their Idios dapp or get_key action.
        payment: Payment amount in groth (1 BEAM = 100,000,000 groth).
        asset_id: 0 for BEAM, 47 for NPH (USD-pegged stablecoin).
        expiry_block: Block height when contract expires. Use current_block + 50000 for ~7 days.
        result_hash: SHA-256 hash of the expected deliverable file (64-char hex string).
        subnet_id: Subnet identifier (default 1).
        epoch: Epoch (default 1).

    Returns confirmation once contract is on chain, or error message.
    """
    args = "role=user,action=create_a," + _build_args([
        ("job_id", job_id),
        ("subnet_id", subnet_id),
        ("epoch", epoch),
        ("expiry_block", expiry_block),
        ("payment", payment),
        ("asset_id", asset_id),
        ("node_pk", worker_pubkey),
        ("result_hash", result_hash),
    ])
    ok, parsed, err = _call_shader(args)
    if not ok:
        return "Error creating contract {}: {}".format(job_id, err)
    return "Contract {} created (Mode A). Payment {} groth locked. Result hash locked: {}. Worker must now commit collateral.".format(
        job_id, payment, result_hash
    )


@mcp.tool()
def commit_collateral(job_id: int, collateral: int) -> str:
    """
    Commit collateral to an Open Idios contract as the worker (Bob).

    This locks your collateral into escrow alongside the requester's payment.
    Both sides now have funds at risk. After committing, the contract moves
    to Active status and you can submit delivery.

    You must be the worker whose pubkey was used to create the contract.
    The asset type is read from chain, not from this call.

    Args:
        job_id: The contract ID to commit to.
        collateral: Amount in groth to lock as collateral. Typically 50% of payment.
            If you lose a dispute, you lose this collateral.

    Returns confirmation once collateral is on chain, or error message.
    """
    job_data = json.loads(view_contract(job_id))
    if "error" in str(job_data).lower():
        return "Cannot commit: could not view contract {}".format(job_id)
    asset_id = job_data.get("asset_id", 0)
    args = "role=user,action=commit," + _build_args([
        ("job_id", job_id),
        ("collateral", collateral),
        ("asset_id", asset_id),
    ])
    ok, parsed, err = _call_shader(args)
    if not ok:
        return "Error committing collateral to contract {}: {}".format(job_id, err)
    return "Collateral {} groth committed to contract {}. Contract is now Active. Proceed with work and submit delivery when done.".format(
        collateral, job_id
    )


@mcp.tool()
def submit_delivery(job_id: int, delivery_hash: str) -> str:
    """
    Submit delivery hash for an Active Idios contract as the worker (Bob).

    For Mode A: if delivery_hash matches the result_hash locked at creation,
    the contract auto-settles immediately and you can claim funds.

    For Mode B: contract moves to AwaitingApproval. The requester has
    review_window_blocks to approve or dispute. If they do nothing,
    call claim_after_timeout once the window expires.

    Send the actual deliverable file to the requester off-chain (via whatever
    channel you agreed). The file never touches the blockchain.

    Args:
        job_id: The contract ID to submit delivery for.
        delivery_hash: SHA-256 hash of your deliverable (64-char hex string).
            Generate with: sha256sum yourfile (Linux/Mac) or
            Get-FileHash yourfile -Algorithm SHA256 (Windows PowerShell).

    Returns confirmation of submission, or error message.
    """
    job_data = json.loads(view_contract(job_id))
    if "error" in str(job_data).lower():
        return "Cannot submit delivery: could not view contract {}".format(job_id)
    payment = job_data.get("payment", 0)
    collateral = job_data.get("collateral", 0)
    mode = job_data.get("mode", 66)
    asset_id = job_data.get("asset_id", 0)
    args = "role=user,action=submit_delivery," + _build_args([
        ("job_id", job_id),
        ("delivery_hash", delivery_hash),
        ("mode", mode),
        ("payment", payment),
        ("collateral", collateral),
        ("asset_id", asset_id),
    ])
    ok, parsed, err = _call_shader(args)
    if not ok:
        return "Error submitting delivery for contract {}: {}".format(job_id, err)
    mode_name = "A (hash-verified)" if mode == 65 else "B (reviewed)"
    return "Delivery submitted for contract {} (Mode {}). Hash: {}. For Mode A, check contract status, it may have auto-settled. For Mode B, requester must approve or dispute within the review window.".format(
        job_id, mode_name, delivery_hash
    )


@mcp.tool()
def approve_delivery(job_id: int) -> str:
    """
    Approve a delivered Mode B contract as the requester (Alice).

    Use this after reviewing the worker's deliverable and confirming it meets
    the agreed specification. Once approved, the worker can claim their payment
    plus collateral.

    Only use after thoroughly testing the deliverable. Approval cannot be
    reversed once confirmed on chain.

    Args:
        job_id: The contract ID to approve.

    Returns confirmation of approval, or error message.
    """
    args = "role=user,action=approve," + _build_args([("job_id", job_id)])
    ok, parsed, err = _call_shader(args)
    if not ok:
        return "Error approving contract {}: {}".format(job_id, err)
    return "Contract {} approved. Worker can now claim payment and collateral.".format(job_id)


@mcp.tool()
def dispute_delivery(job_id: int) -> str:
    """
    Dispute a delivered Mode B contract as the requester (Alice).

    Use this if the deliverable does not meet the agreed specification.
    Filing a dispute locks the dispute_fee from the contract. An arbitrator
    will review and resolve on-chain.

    After disputing, contact the arbitrator at @tappyoak on Telegram or
    Discord with the contract ID, your role (requester), and a description
    of why the work does not meet spec. The arbitrator's decision is final.

    If the arbitrator sides with you: you receive payment + worker collateral
    + dispute fee. If they side with the worker: worker receives everything
    including your dispute fee.

    Args:
        job_id: The contract ID to dispute.

    Returns confirmation of dispute filing, or error message.
    """
    args = "role=user,action=dispute," + _build_args([("job_id", job_id)])
    ok, parsed, err = _call_shader(args)
    if not ok:
        return "Error filing dispute for contract {}: {}".format(job_id, err)
    return "Dispute filed for contract {}. Contact arbitrator @tappyoak on Telegram or Discord with contract ID and description of the issue.".format(
        job_id
    )


@mcp.tool()
def claim_funds(job_id: int) -> str:
    """
    Claim funds from a Settled or Resolved Idios contract.

    Call this after the contract reaches one of these states:
    - Settled (status=4): worker claims payment + collateral
    - ResolvedToBob (status=7): worker won dispute, claims payment + collateral + dispute_fee
    - ResolvedToAlice (status=6): requester won dispute, claims payment + dispute_fee
    - Refunded (status=5): requester claims payment back

    The amounts are read from chain automatically.

    Args:
        job_id: The contract ID to claim from.

    Returns confirmation of claim, or error message.
    """
    job_data = json.loads(view_contract(job_id))
    if "error" in str(job_data).lower():
        return "Cannot claim: could not view contract {}".format(job_id)

    status = int(job_data.get("status", -1))
    payment = int(job_data.get("payment", 0))
    collateral = int(job_data.get("collateral", 0))
    dispute_fee = int(job_data.get("dispute_fee", 0))
    asset_id = int(job_data.get("asset_id", 0))

    STATUS_SETTLED = 4
    STATUS_REFUNDED = 5
    STATUS_RESOLVED_TO_ALICE = 6
    STATUS_RESOLVED_TO_BOB = 7

    if status == STATUS_SETTLED:
        total = payment + collateral
    elif status == STATUS_RESOLVED_TO_BOB:
        total = payment + collateral + dispute_fee
    elif status == STATUS_RESOLVED_TO_ALICE:
        total = payment + dispute_fee
    elif status == STATUS_REFUNDED:
        total = payment
    else:
        return "Contract {} is not in a claimable state. Current status: {} ({}). Claimable states: Settled, ResolvedToAlice, ResolvedToBob, Refunded.".format(
            job_id, status, _status_name(status)
        )

    args = "role=user,action=claim," + _build_args([
        ("job_id", job_id),
        ("total", total),
        ("asset_id", asset_id),
    ])
    ok, parsed, err = _call_shader(args)
    if not ok:
        return "Error claiming from contract {}: {}".format(job_id, err)
    return "Claimed {} groth from contract {}. Funds will appear in wallet within the next block.".format(
        total, job_id
    )


@mcp.tool()
def claim_after_timeout(job_id: int) -> str:
    """
    Claim funds as the worker after the review window expires without requester action.

    Use this for Mode B contracts in AwaitingApproval status where the requester
    has not approved or disputed within the review_window_blocks set at creation.
    This protects workers from requesters going silent indefinitely.

    Check view_contract first to confirm the review window has passed before
    calling this. If the window has not yet expired, the call will fail on chain.

    Args:
        job_id: The contract ID to claim from after timeout.

    Returns confirmation of claim, or error message.
    """
    args = "role=user,action=claim_after_timeout," + _build_args([("job_id", job_id)])
    ok, parsed, err = _call_shader(args)
    if not ok:
        return "Error claiming after timeout for contract {}: {}".format(job_id, err)
    return "Claim after timeout submitted for contract {}. If the review window has passed, funds will be claimable.".format(
        job_id
    )


@mcp.tool()
def refund_contract(job_id: int) -> str:
    """
    Refund an expired Open contract as the requester (Alice).

    Use this if the contract is in Open status (worker never committed collateral)
    and the expiry_block has passed. Recovers your locked payment.

    Note: refund only unwinds the contract, it does not penalise the worker.
    The worker also gets their collateral back (they never committed any).
    If a worker committed collateral and then abandoned the job, you cannot
    refund via this path. The contract must go through dispute resolution instead.

    Args:
        job_id: The contract ID to refund.

    Returns confirmation of refund, or error message.
    """
    job_data = json.loads(view_contract(job_id))
    if "error" in str(job_data).lower():
        return "Cannot refund: could not view contract {}".format(job_id)
    payment = int(job_data.get("payment", 0))
    collateral = int(job_data.get("collateral", 0))
    asset_id = int(job_data.get("asset_id", 0))
    args = "role=user,action=refund," + _build_args([
        ("job_id", job_id),
        ("payment", payment),
        ("collateral", collateral),
        ("asset_id", asset_id),
    ])
    ok, parsed, err = _call_shader(args)
    if not ok:
        return "Error refunding contract {}: {}".format(job_id, err)
    return "Refund submitted for contract {}. Payment will be returned to your wallet.".format(job_id)


def load_config(path: str) -> dict:
    with open(path) as f:
        cfg = json.load(f)
    required = ["beam_wallet_binary", "shader_app_file", "wallet_path", "node_addr", "cid"]
    missing = [k for k in required if k not in cfg]
    if missing:
        raise ValueError("Config missing required keys: " + ", ".join(missing))
    for p in ["beam_wallet_binary", "shader_app_file", "wallet_path"]:
        if not os.path.exists(cfg[p]):
            raise ValueError("Path does not exist: " + cfg[p])
    return cfg


def main():
    parser = argparse.ArgumentParser(description="Idios MCP Server")
    parser.add_argument("--config", required=True, help="Path to idios_mcp_config.json")
    args = parser.parse_args()

    global _cfg, _password

    try:
        _cfg = load_config(args.config)
    except Exception as e:
        print("Config error: " + str(e), file=sys.stderr)
        sys.exit(1)

    try:
        _password = getpass.getpass("Wallet password: ")
    except (KeyboardInterrupt, EOFError):
        sys.exit(0)

    # Run the MCP server via stdio transport.
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

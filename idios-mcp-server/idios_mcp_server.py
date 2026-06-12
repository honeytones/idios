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
      "cid": "ed788e2f03faf0a461d110725509aa49b93671007bb554ea4baea077236ac3cb"
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
import threading
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
    9: "Voided",
    10: "Cancelled",
}

# Global config and password set at startup before serving.
_cfg: dict = {}
_password: str = ""

# The CLI wallet allows only one beam-wallet process at a time (wallet.db
# lock). Serialise every wallet subprocess so parallel tool calls from the
# agent queue instead of deadlocking against each other.
_shader_lock = threading.Lock()


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
        with _shader_lock:
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


def _view_state(job_id: int):
    """Return parsed contract state dict, or None if the view failed.
    view_contract returns a plain error string (not JSON) on failure, so
    parse defensively rather than letting json.loads raise."""
    raw = view_contract(job_id)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


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
        "wait for on-chain confirmation and may take 1-2 minutes. "
        "If a dispute is never resolved within the arbitrator timeout, recover "
        "funds with void_dispute, then void_claim_requester or void_claim_node."
    )
)


@mcp.tool()
def view_contract(job_id: int) -> str:
    """
    Get the current on-chain state of an Idios contract.

    Returns all contract fields including status, payment, collateral,
    dispute_fee, delivery_hash, expiry_block, mode, and asset_id.

    Status values: Open(0), Active(1), AwaitingApproval(2), Disputed(3),
    Settled(4), Refunded(5), ResolvedToAlice(6), ResolvedToBob(7), Closed(8),
    Voided(9).

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
    asset_id = int(job.get("asset_id", 0))
    job["asset_name"] = {0: "BEAM", 47: "NPH"}.get(asset_id, "asset {}".format(asset_id))
    return json.dumps(job, indent=2)


@mcp.tool()
def get_chain_info() -> str:
    """
    Read the current Beam block height from the wallet's node.

    Call this before creating a contract so you can choose a future
    expiry_block (the contract requires expiry_block to be in the future).
    Add a margin to the returned height: current + 50000 is roughly 7 days,
    current + 2000 is a short test window.

    Returns the current block height, or an error message.
    """
    import os, subprocess
    cmd = [
        _cfg["beam_wallet_binary"], "info",
        "--node_addr=" + _cfg["node_addr"],
        "--wallet_path=" + _cfg["wallet_path"],
        "--pass=" + _password,
    ]
    try:
        with _shader_lock:
            result = subprocess.run(
                cmd,
                cwd=os.path.dirname(_cfg["beam_wallet_binary"]),
                capture_output=True,
                text=True,
                timeout=120,
            )
    except Exception as e:
        return "Error reading chain info: " + str(e)
    out = result.stdout + result.stderr
    for line in out.splitlines():
        if "Current height" in line:
            digits = "".join(ch for ch in line if ch.isdigit())
            if digits:
                return "Current block height: {}. For expiry_block add a margin (current + 50000 is about 7 days, current + 2000 is a short test).".format(int(digits))
    return "Could not read the current height from wallet info."


@mcp.tool()
def get_key() -> str:
    """
    Get your own Beam pubkey for the Idios contract.

    This is the value a counterparty uses to name you in a contract: it goes
    in worker_pubkey when a requester calls create_contract_a or
    create_contract_b. It is derived from your wallet and the Idios contract,
    so it stays the same for this wallet on this contract.

    Share it with a counterparty so they can create a contract with you.
    This call is read-only and does not require wallet funds.

    Returns your pubkey as a hex string, or an error message.
    """
    args = "role=user,action=get_key," + _build_args([])
    ok, parsed, err = _call_shader(args)
    if not ok or parsed is None:
        return "Error getting your pubkey: {}".format(err)
    pk = None
    if isinstance(parsed.get("key"), dict):
        pk = parsed["key"].get("pub_key")
    if not pk:
        pk = parsed.get("pub_key") or parsed.get("pubkey")
    if not pk:
        return "Got a response but no pub_key field. Raw output: {}".format(json.dumps(parsed))
    return "Your Idios pubkey (share this with counterparties): {}".format(pk)


@mcp.tool()
def create_contract_b(
    job_id: int,
    worker_pubkey: str,
    payment: int,
    asset_id: int,
    expiry_block: int,
    dispute_fee: int,
    review_window_blocks: int = 0,
    required_collateral: int = 0,
    spec_hash: str = "",
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
            2000 blocks is roughly 33 hours. Pass 0 (or omit) to use the
            contract default set at deploy time.
        dispute_fee: Amount requester locks if they dispute. Lost if dispute goes against them.
        required_collateral: Minimum collateral in groth the worker must commit.
            The contract rejects any commit below this floor. 0 (default) = no floor.
        spec_hash: Optional SHA-256 hash (64 char hex) of the job specification,
            stored on chain for later reference. Omit or pass "" for none.
        subnet_id: Subnet identifier (default 1).
        epoch: Epoch (default 1).

    Returns confirmation once contract is on chain, or error message.
    """
    parts = [
        ("job_id", job_id),
        ("subnet_id", subnet_id),
        ("epoch", epoch),
        ("expiry_block", expiry_block),
        ("review_window_blocks", review_window_blocks),
        ("payment", payment),
        ("dispute_fee", dispute_fee),
        ("required_collateral", required_collateral),
        ("asset_id", asset_id),
        ("node_pk", worker_pubkey),
    ]
    if spec_hash:
        parts.append(("spec_hash", spec_hash))
    args = "role=user,action=create_b," + _build_args(parts)
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
    required_collateral: int = 0,
    spec_hash: str = "",
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
        required_collateral: Minimum collateral in groth the worker must commit.
            The contract rejects any commit below this floor. 0 (default) = no floor.
        spec_hash: Optional SHA-256 hash (64 char hex) of the job specification,
            stored on chain for later reference. Omit or pass "" for none.
        subnet_id: Subnet identifier (default 1).
        epoch: Epoch (default 1).

    Returns confirmation once contract is on chain, or error message.
    """
    parts = [
        ("job_id", job_id),
        ("subnet_id", subnet_id),
        ("epoch", epoch),
        ("expiry_block", expiry_block),
        ("payment", payment),
        ("asset_id", asset_id),
        ("node_pk", worker_pubkey),
        ("result_hash", result_hash),
    ]
    if spec_hash:
        parts.append(("spec_hash", spec_hash))
    args = "role=user,action=create_a," + _build_args(parts)
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
    job_data = _view_state(job_id)
    if job_data is None:
        return "Cannot commit: could not read contract {} state.".format(job_id)
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
    job_data = _view_state(job_id)
    if job_data is None:
        return "Cannot submit delivery: could not read contract {} state.".format(job_id)
    mode = job_data.get("mode", 66)
    args = "role=user,action=submit_delivery," + _build_args([
        ("job_id", job_id),
        ("delivery_hash", delivery_hash),
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
def mutual_cancel(job_id: int) -> str:
    """
    Cancel an Active or AwaitingApproval contract by mutual agreement, with
    everyone made whole.

    The contract requires signatures from BOTH the requester and the worker
    on one transaction. In the current implementation both signatures come
    from this wallet, so mutual cancel works when the same wallet holds both
    roles (self dealing tests, or contracts where both pubkeys were derived
    from this wallet). True cross wallet co signing is not implemented yet.

    On success the payment returns to the requester and the collateral to
    the worker in the cancel transaction itself; there is no separate claim
    step. The contract reaches the terminal Cancelled state. Not allowed
    from Open (nothing to mutually cancel) or Disputed (the arbitrator owns
    the outcome).

    Args:
        job_id: The contract ID to cancel.

    Returns confirmation of cancellation, or error message.
    """
    args = "role=user,action=mutual_cancel," + _build_args([("job_id", job_id)])
    ok, parsed, err = _call_shader(args)
    if not ok:
        return "Error cancelling contract {}: {}".format(job_id, err)
    return "Contract {} cancelled by mutual agreement. Payment returned to requester, collateral to worker, in the cancel transaction.".format(job_id)


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
    - ResolvedToAlice (status=6): requester won dispute, claims payment + collateral + dispute_fee
    A refunded contract (status=5) returns funds directly and needs no claim.

    The amounts are read from chain automatically.

    Args:
        job_id: The contract ID to claim from.

    Returns confirmation of claim, or error message.
    """
    job_data = _view_state(job_id)
    if job_data is None:
        return "Cannot claim: could not read contract {} state.".format(job_id)

    status = int(job_data.get("status", -1))
    payment = int(job_data.get("payment", 0))
    collateral = int(job_data.get("collateral", 0))
    dispute_fee = int(job_data.get("dispute_fee", 0))
    asset_id = int(job_data.get("asset_id", 0))
    mode = int(job_data.get("mode", 66))

    STATUS_SETTLED = 4
    STATUS_RESOLVED_TO_ALICE = 6
    STATUS_RESOLVED_TO_BOB = 7
    STATUS_VOIDED = 9

    if status == STATUS_SETTLED and mode == 65:
        return "Contract {} is Mode A (hash-verified) and settled automatically at delivery. Funds were released when the matching hash was submitted, so there is nothing to claim.".format(job_id)
    if status == STATUS_SETTLED:
        total = payment + collateral
    elif status == STATUS_RESOLVED_TO_BOB:
        total = payment + collateral + dispute_fee
    elif status == STATUS_RESOLVED_TO_ALICE:
        total = payment + collateral + dispute_fee
    elif status == STATUS_VOIDED:
        return "Contract {} is Voided (arbitrator timeout). Use void_claim_requester to reclaim your payment if you are the requester, or void_claim_node to reclaim your collateral if you are the worker.".format(job_id)
    else:
        return "Contract {} is not in a claimable state. Current status: {} ({}). Claimable states: Settled, ResolvedToAlice, ResolvedToBob. A refunded contract returns funds directly, no claim needed.".format(
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
    Refund an expired Idios contract as the requester (Alice).

    Valid in two situations, both requiring the contract's expiry_block to
    have passed:
    - Open (worker never committed collateral): your payment is returned.
    - Active (worker committed collateral but never delivered): your payment
      is returned, and the worker's collateral is forfeited to the protocol
      treasury as a non-delivery penalty. You never receive the worker's
      collateral yourself, so a tight expiry cannot be used to seize their
      stake.

    Funds are returned in the refund transaction itself. No separate claim
    is needed afterwards. Refund is not possible once a delivery has been
    submitted (AwaitingApproval or later); use approve, dispute, or the
    dispute resolution flow instead.

    Args:
        job_id: The contract ID to refund.

    Returns confirmation of refund, or error message.
    """
    args = "role=user,action=refund," + _build_args([("job_id", job_id)])
    ok, parsed, err = _call_shader(args)
    if not ok:
        return "Error refunding contract {}: {}".format(job_id, err)
    return "Refund submitted for contract {}. Payment will be returned to your wallet. If the worker had committed collateral, it is forfeited to the protocol treasury.".format(job_id)


@mcp.tool()
def void_dispute(job_id: int) -> str:
    """
    Void a Disputed contract that the arbitrator never resolved.

    This is the recovery path for a stale dispute. It is permissionless:
    anyone can call it once arbitrator_timeout_blocks have passed since the
    dispute was filed. The condition is strict: current block height must be
    GREATER than dispute_filed_block + arbitrator_timeout_blocks. A call
    exactly on the boundary fails; it succeeds from the next block.

    arbitrator_timeout_blocks is a per contract deployment parameter, not a
    universal constant. Never assume a value: compute eligibility from
    dispute_filed_block in view_contract plus the timeout of the contract
    this server is configured for. On the v4 production contract it is
    20160 blocks, roughly 14 days; test deployments may use far shorter
    values.

    Voiding moves the contract to Voided status. Neither party wins: each
    side then reclaims its own principal. After voiding, the requester calls
    void_claim_requester to reclaim the payment and the worker calls
    void_claim_node to reclaim the collateral. The dispute fee is forfeited
    to the protocol treasury.

    Check view_contract for dispute_filed_block and get_chain_info for the
    current height before calling. If the timeout has not passed, the call
    fails on chain.

    Args:
        job_id: The Disputed contract ID to void.

    Returns confirmation of voiding, or error message.
    """
    args = "role=user,action=void_dispute," + _build_args([("job_id", job_id)])
    ok, parsed, err = _call_shader(args)
    if not ok:
        return "Error voiding dispute for contract {}: {}".format(job_id, err)
    return "Contract {} voided. The requester can reclaim the payment via void_claim_requester and the worker can reclaim collateral via void_claim_node.".format(job_id)


@mcp.tool()
def void_claim_requester(job_id: int) -> str:
    """
    Reclaim your payment from a Voided contract as the requester (Alice).

    Use this after a stale dispute was voided via void_dispute. Returns the
    full payment you locked at contract creation. The amount is read from
    chain and can only be claimed once. Your dispute fee is not returned;
    it is forfeited to the protocol treasury.

    Args:
        job_id: The Voided contract ID to reclaim payment from.

    Returns confirmation of the claim, or error message.
    """
    args = "role=user,action=void_claim_requester," + _build_args([("job_id", job_id)])
    ok, parsed, err = _call_shader(args)
    if not ok:
        return "Error reclaiming payment from voided contract {}: {}".format(job_id, err)
    return "Payment reclaimed from voided contract {}. Funds will appear in your wallet within the next block.".format(job_id)


@mcp.tool()
def void_claim_node(job_id: int) -> str:
    """
    Reclaim your collateral from a Voided contract as the worker (Bob).

    Use this after a stale dispute was voided via void_dispute. Returns the
    full collateral you committed. The amount is read from chain and can
    only be claimed once.

    Args:
        job_id: The Voided contract ID to reclaim collateral from.

    Returns confirmation of the claim, or error message.
    """
    args = "role=user,action=void_claim_node," + _build_args([("job_id", job_id)])
    ok, parsed, err = _call_shader(args)
    if not ok:
        return "Error reclaiming collateral from voided contract {}: {}".format(job_id, err)
    return "Collateral reclaimed from voided contract {}. Funds will appear in your wallet within the next block.".format(job_id)


@mcp.tool()
def treasury_sweep(job_id: int) -> str:
    """
    Sweep forfeited funds to the protocol treasury.

    Only succeeds if this wallet holds the treasury key (the wallet that
    deployed the contract). Collects exactly two kinds of forfeited funds:
    the worker's collateral from a Refunded contract that went through the
    Active path (worker committed, never delivered), or the dispute fee
    from a Voided contract. Each can be swept once. The call fails for any
    other wallet or any other contract state.

    Args:
        job_id: The Refunded or Voided contract ID to sweep.

    Returns confirmation of the sweep, or error message.
    """
    args = "role=treasury,action=sweep," + _build_args([("job_id", job_id)])
    ok, parsed, err = _call_shader(args)
    if not ok:
        return "Error sweeping contract {}: {}".format(job_id, err)
    return "Treasury sweep submitted for contract {}. Forfeited funds will appear in the treasury wallet within the next block.".format(job_id)


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

    env_pass = os.environ.get("IDIOS_WALLET_PASS")
    if env_pass:
        _password = env_pass
    else:
        try:
            _password = getpass.getpass("Wallet password: ")
        except (KeyboardInterrupt, EOFError):
            sys.exit(0)

    # Run the MCP server via stdio transport.
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

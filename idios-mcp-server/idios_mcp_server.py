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
      "node_addr": "eu-node01.mainnet.beam.mw:8100",
      "cid": "41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f"
    }

The cid above is the live Idios v2 contract on Beam mainnet. shader_app_file
must point at the repo's current idios_app.wasm (the v2 app shader); an old
wasm will not know the v2 actions.

The server uses stdio transport, meaning the agent framework starts it as a
subprocess and communicates via stdin/stdout. This is the standard local MCP
pattern and keeps the wallet password off the network.

Notes:
    - beam-wallet shader exits rc=1 even on success. Trust parsed output not rc.
    - State-changing calls (commit, submit_delivery, approve, dispute, claim)
      usually confirm in one to two minutes, occasionally several. SHADER_TIMEOUT_SECONDS=600.
    - view_contract is read-only and fast.
    - The CID in config must match the deployed Idios contract (production v2
      is 41ef8be5...).
    - expiry_block must be in the future. Use current_block + 10000 for ~7 days.
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


def _view_dispute_state(job_id: int):
    """Return the parsed view_dispute dict for a job, or None on failure.
    Read only, works for any Disputed or Resolved job."""
    args = "role=manager,action=view_dispute," + _build_args([("job_id", job_id)])
    ok, parsed, err = _call_shader(args)
    if not ok or parsed is None or not isinstance(parsed, dict):
        return None
    return parsed.get("dispute") or parsed


# ----------------------------------------------------------------
# Local observation ledger (worker reputation, phase 1)
# ----------------------------------------------------------------
# Every contract this server views is recorded per worker pubkey in a small
# local json file. Reputation here is LOCAL AND OBSERVED by design: it is
# what this wallet has personally seen a worker do, so a stranger cannot
# inflate it by self dealing elsewhere. The global, unfakeable half of the
# picture is the on chain bond (view_worker_bond).

_config_path = None


def _ledger_path():
    custom = _cfg.get("reputation_ledger_path")
    if custom:
        return custom
    base = os.path.dirname(os.path.abspath(_config_path)) if _config_path else "."
    return os.path.join(base, "idios_reputation_ledger.json")


def _ledger_load():
    try:
        with open(_ledger_path()) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _ledger_save(data):
    try:
        with open(_ledger_path(), "w") as f:
            json.dump(data, f, indent=1)
    except Exception as e:
        logging.warning("reputation ledger write failed: %s", e)


def _observe_job(job):
    """Record a viewed job against its worker pubkey. Never raises."""
    try:
        pk = job.get("node_pk")
        job_id = job.get("job_id")
        if not pk or job_id is None:
            return
        ledger = _ledger_load()
        rec = ledger.setdefault(str(pk), {"jobs": {}})
        rec["jobs"][str(job_id)] = {
            "status": int(job.get("status", -1)),
            "payment": int(job.get("payment", 0)),
            "collateral": int(job.get("collateral", 0)),
            "asset_id": int(job.get("asset_id", 0)),
            "mode": int(job.get("mode", 0)),
        }
        _ledger_save(ledger)
    except Exception as e:
        logging.warning("reputation observation failed: %s", e)


def _reputation_stats(pk, ledger):
    """Bucket everything observed for a worker pubkey. Pure function."""
    jobs = ledger.get(str(pk), {}).get("jobs", {})
    stats = {
        "jobs_observed": len(jobs),
        "completed": 0,           # Settled(4) or Closed(8)
        "disputes_lost": 0,       # ResolvedToAlice(6), worker lost
        "disputes_won": 0,        # ResolvedToBob(7), worker won
        "abandoned": 0,           # Refunded(5) with collateral locked: committed then went silent
        "cancelled": 0,           # Cancelled(10), mutual, neutral
        "voided": 0,              # Voided(9), arbitration timed out, neutral
        "in_flight": 0,           # Open/Active/AwaitingApproval/Disputed
        "completed_volume": 0,    # groth across completed jobs
    }
    for j in jobs.values():
        st = j.get("status", -1)
        if st in (4, 8):
            stats["completed"] += 1
            stats["completed_volume"] += j.get("payment", 0)
        elif st == 6:
            stats["disputes_lost"] += 1
        elif st == 7:
            stats["disputes_won"] += 1
        elif st == 5 and j.get("collateral", 0) > 0:
            stats["abandoned"] += 1
        elif st == 10:
            stats["cancelled"] += 1
        elif st == 9:
            stats["voided"] += 1
        elif st in (0, 1, 2, 3):
            stats["in_flight"] += 1
    return stats


def _suggest_collateral(payment, bond_state, bond_stake, stats):
    """Suggested collateral for a job of the given payment, with reasons.
    Transparent heuristics, not a guarantee. Returns (amount, reasons)."""
    reasons = []
    pct = 50
    reasons.append("baseline 50% of payment for an unknown or lightly proven worker")
    bad_history = stats["disputes_lost"] > 0 or stats["abandoned"] > 0
    if bond_state == 3:
        pct = 100
        reasons.append("bond SLASHED: this key lost an arbitrated dispute and forfeited its stake; demand full collateral or do not hire")
        return payment * pct // 100, reasons
    if bad_history:
        pct = 100
        reasons.append("this server has observed {} lost dispute(s) and {} abandoned job(s) on this key: demand full collateral".format(
            stats["disputes_lost"], stats["abandoned"]))
        return payment * pct // 100, reasons
    if bond_state == 0 and bond_stake > 0:
        if bond_stake >= 2 * payment and stats["completed"] >= 3:
            pct = 15
            reasons.append("live bond covers 2x this payment and {} clean completions observed: 15%".format(stats["completed"]))
        elif bond_stake >= payment:
            pct = 25
            reasons.append("live bond covers this payment: 25% (losing a dispute costs them the whole bond)")
        else:
            pct = 40
            reasons.append("live bond exists but is smaller than this payment: 40%")
    elif stats["completed"] >= 3:
        pct = 35
        reasons.append("no bond, but {} clean completions observed by this server: 35%".format(stats["completed"]))
    return payment * pct // 100, reasons


# Initialise FastMCP server.
mcp = FastMCP(
    "idios",
    instructions=(
        "Idios is a private escrow protocol on Beam MimbleWimble. "
        "Use these tools to create and manage private work contracts. "
        "Both sides lock funds before work starts. Amounts and parties stay private. "
        "All amounts are in groth (1 BEAM = 100,000,000 groth, NPH asset_id=47 same unit). "
        "expiry_block must be in the future: use current block + 10000 for roughly 7 days. "
        "State-changing calls (commit, submit_delivery, approve, dispute, claim) "
        "wait for on-chain confirmation, usually one to two minutes, occasionally several. "
        "If a dispute is never resolved within the arbitrator timeout, recover "
        "funds with void_dispute, then void_claim_requester or void_claim_node. "
        "Disputes are resolved by arbitrators voting on chain; the winner "
        "receives payment + collateral and the dispute fee goes to the voting "
        "arbitrators. Workers can post a slashable reputation bond with "
        "worker_register; check any worker's bond with view_worker_bond."
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
    Voided(9), Cancelled(10).

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
    _observe_job(job)
    return json.dumps(job, indent=2)


@mcp.tool()
def get_chain_info() -> str:
    """
    Read the current Beam block height from the wallet's node.

    Call this before creating a contract so you can choose a future
    expiry_block (the contract requires expiry_block to be in the future).
    Add a margin to the returned height: current + 10000 is roughly 7 days,
    current + 2000 is a short test window.

    Returns the current block height, or an error message.
    """
    import os, re, subprocess
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
    # The wallet DB's "Current height" can be DAYS stale until the wallet
    # syncs. The sync log lines ("Sync up to N-hash", "Current state is
    # N-hash") carry the node tip. Collect every height signal and take the
    # maximum, which is correct whichever line is stale.
    heights = []
    for line in out.splitlines():
        if "Current height" in line:
            digits = "".join(ch for ch in line if ch.isdigit())
            if digits:
                heights.append(int(digits))
        m = re.search(r"(?:Sync up to|Current state is)\s+(\d+)-", line)
        if m:
            heights.append(int(m.group(1)))
    if heights:
        return "Current block height: {}. For expiry_block add a margin (current + 10000 is about 7 days, current + 2000 is a short test).".format(max(heights))
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
        expiry_block: Block height when contract expires. Use current_block + 10000 for ~7 days.
        review_window_blocks: How long requester has to approve/dispute after delivery.
            2000 blocks is roughly 33 hours. Pass 0 (or omit) to use the
            contract default set at deploy time.
        dispute_fee: Amount the requester locks if they file a dispute. It pays
            the voting arbitrators regardless of outcome; neither party gets it back.
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
        expiry_block: Block height when contract expires. Use current_block + 10000 for ~7 days.
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
def batch_create_contracts(specs: list) -> str:
    """
    Create up to 50 Mode B escrow contracts in ONE wallet transaction.

    This is swarm payroll: an orchestrator agent that has split work across
    many worker agents locks payment for all of them at once, privately.
    One transaction, one network fee, every child contract created Open.
    Each worker then commits collateral to their own contract and the normal
    Mode B lifecycle (deliver, review, approve or dispute, claim) runs per
    contract from there.

    All contracts in the batch are created by this wallet as the requester.
    The wallet must hold the SUM of all payments plus one network fee.
    Batch creation is Mode B only, and specs cannot carry required_collateral
    or spec_hash; create those contracts individually with create_contract_b.

    Each spec is an object with these fields:
        job_id (int, required): unique ID you choose, unused on chain, and
            not repeated inside the batch.
        worker_pubkey (str, required): that worker's pubkey for this contract
            (from their get_key).
        payment (int, required): payment in groth (1 BEAM = 100,000,000).
        asset_id (int, required): 0 for BEAM, 47 for NPH.
        expiry_block (int, required): future block height. current + 10000
            is roughly 7 days.
        dispute_fee (int, required): locked if the requester disputes; it
            pays the voting arbitrators win or lose.
        review_window_blocks (int, optional, default 0): approval window
            after delivery. 0 uses the contract default.
        subnet_id (int, optional, default 1).
        epoch (int, optional, default 1).

    Args:
        specs: List of 1 to 50 spec objects as described above.

    Returns a summary of the created contracts, or an error message.
    """
    if not isinstance(specs, list) or len(specs) == 0:
        return "Error: specs must be a non-empty list of contract spec objects."
    if len(specs) > 50:
        return "Error: batch is capped at 50 contracts per transaction (got {}). Split into multiple batches.".format(len(specs))

    required = ["job_id", "worker_pubkey", "payment", "asset_id", "expiry_block", "dispute_fee"]
    seen_ids = set()
    total_payment = 0
    parts = [("batch_count", len(specs))]
    for i, spec in enumerate(specs):
        if not isinstance(spec, dict):
            return "Error: spec {} is not an object.".format(i)
        missing = [k for k in required if k not in spec]
        if missing:
            return "Error: spec {} (job_id {}) missing required fields: {}.".format(
                i, spec.get("job_id", "?"), ", ".join(missing))
        try:
            job_id = int(spec["job_id"])
            payment = int(spec["payment"])
            asset_id = int(spec["asset_id"])
            expiry_block = int(spec["expiry_block"])
            dispute_fee = int(spec["dispute_fee"])
            review_window = int(spec.get("review_window_blocks", 0))
            subnet_id = int(spec.get("subnet_id", 1))
            epoch = int(spec.get("epoch", 1))
        except (TypeError, ValueError):
            return "Error: spec {} has a non-integer value in an integer field.".format(i)
        if job_id in seen_ids:
            return "Error: job_id {} appears more than once in the batch.".format(job_id)
        seen_ids.add(job_id)
        if payment <= 0:
            return "Error: spec {} (job_id {}) has payment <= 0.".format(i, job_id)
        if dispute_fee <= 0:
            return "Error: spec {} (job_id {}) has dispute_fee <= 0 (the contract requires a positive fee).".format(i, job_id)
        worker_pubkey = str(spec["worker_pubkey"]).strip()
        if not worker_pubkey:
            return "Error: spec {} (job_id {}) has an empty worker_pubkey.".format(i, job_id)
        total_payment += payment
        parts.append(("job_id_" + str(i), job_id))
        parts.append(("subnet_id_" + str(i), subnet_id))
        parts.append(("epoch_" + str(i), epoch))
        parts.append(("expiry_block_" + str(i), expiry_block))
        parts.append(("review_window_blocks_" + str(i), review_window))
        parts.append(("payment_" + str(i), payment))
        parts.append(("dispute_fee_" + str(i), dispute_fee))
        parts.append(("asset_id_" + str(i), asset_id))
        parts.append(("node_pk_" + str(i), worker_pubkey))

    args = "role=user,action=batch_create_b," + _build_args(parts)
    ok, parsed, err = _call_shader(args)
    if not ok:
        return "Error creating batch of {} contracts: {}".format(len(specs), err)
    id_list = ", ".join(str(s["job_id"]) for s in specs)
    return "Batch created: {} Mode B contracts (job_ids: {}) in one transaction. Total payment locked: {} groth. Each worker must now commit collateral to their own contract.".format(
        len(specs), id_list, total_payment
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
    Filing a dispute locks the dispute_fee from the contract. The registered
    arbitrators then vote on chain to resolve it.

    After disputing, contact the arbitrator at @tappyoak on Telegram or
    Discord with the contract ID, your role (requester), and a description
    of why the work does not meet spec. The resolution is final.

    The dispute winner receives payment + collateral only. The dispute fee
    is split among the consensus voting arbitrators, never awarded to either
    party. Use view_dispute to follow the vote and the payout flags.

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
def view_dispute(job_id: int) -> str:
    """
    Get the on chain dispute record for a Disputed or Resolved contract.

    Use this to follow an arbitration vote and to check payout state after
    resolution. Fields returned:
    - frozen_n, threshold: arbitrator set size and votes needed, frozen when
      the dispute was filed.
    - vc_alice, vc_bob: current vote counts for the requester and the worker.
    - resolution: 0 none yet, 1 the requester (Alice) won, 2 the worker
      (Bob) won.
    - winner_paid: 1 once the winner has claimed. A Resolved contract stays
      at status 6 or 7 forever, so this flag is the ONLY signal that the
      payout happened. Never retry claim_funds when winner_paid is 1.
    - fee_share, fee_remainder, remainder_swept: how the dispute fee was
      split among the consensus voting arbitrators, and whether the
      remainder went to the treasury.
    - bond_encumbered: 1 while this dispute holds a lien on the worker's
      reputation bond (the bond cannot be reclaimed until the dispute ends).

    Read only, needs no wallet funds.

    Args:
        job_id: The contract ID whose dispute to inspect.

    Returns the dispute record as JSON with a one line interpretation, or an
    error message.
    """
    dispute = _view_dispute_state(job_id)
    if dispute is None:
        return "Error viewing dispute for contract {}: no dispute record found (was a dispute ever filed?).".format(job_id)
    resolution = int(dispute.get("resolution", 0))
    winner_paid = int(dispute.get("winner_paid", 0))
    if resolution == 0:
        summary = "No resolution yet: leading side has {} of the {} votes needed.".format(
            max(int(dispute.get("vc_alice", 0)), int(dispute.get("vc_bob", 0))),
            dispute.get("threshold", "?"),
        )
    elif resolution == 1:
        summary = "Resolution 1 means the requester (Alice) won." + (" Winner already paid." if winner_paid else " Winner has not claimed yet.")
    else:
        summary = "Resolution 2 means the worker (Bob) won." + (" Winner already paid." if winner_paid else " Winner has not claimed yet.")
    return json.dumps(dispute, indent=2) + "\n" + summary


@mcp.tool()
def claim_funds(job_id: int) -> str:
    """
    Claim funds from a Settled or Resolved Idios contract.

    Call this after the contract reaches one of these states:
    - Settled (status=4): worker claims payment + collateral
    - ResolvedToBob (status=7): worker won the dispute, claims payment + collateral
    - ResolvedToAlice (status=6): requester won the dispute, claims payment + collateral
    A refunded contract (status=5) returns funds directly and needs no claim.

    The dispute winner receives payment + collateral only. The dispute fee is
    split among the consensus voting arbitrators, never awarded to a party.

    A Resolved contract stays at status 6 or 7 forever, even after the winner
    is paid. This tool checks the winner_paid flag via view_dispute before
    claiming, and reports "already claimed" instead of firing a call that
    would halt on chain.

    The amounts are read from chain by the contract; only job_id is sent.

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
    mode = int(job_data.get("mode", 66))

    STATUS_SETTLED = 4
    STATUS_RESOLVED_TO_ALICE = 6
    STATUS_RESOLVED_TO_BOB = 7
    STATUS_VOIDED = 9

    if status == STATUS_SETTLED and mode == 65:
        return "Contract {} is Mode A (hash verified) and settled automatically at delivery. Funds were released when the matching hash was submitted, so there is nothing to claim.".format(job_id)
    if status == STATUS_SETTLED:
        total = payment + collateral
    elif status in (STATUS_RESOLVED_TO_ALICE, STATUS_RESOLVED_TO_BOB):
        dispute = _view_dispute_state(job_id)
        if dispute is not None and int(dispute.get("winner_paid", 0)) == 1:
            return "Contract {} was already claimed by the dispute winner (winner_paid=1). Status stays {} ({}) forever; there is nothing left to claim.".format(
                job_id, status, _status_name(status)
            )
        total = payment + collateral
    elif status == STATUS_VOIDED:
        return "Contract {} is Voided (arbitrator timeout). Use void_claim_requester to reclaim your payment if you are the requester, or void_claim_node to reclaim your collateral if you are the worker.".format(job_id)
    else:
        return "Contract {} is not in a claimable state. Current status: {} ({}). Claimable states: Settled, ResolvedToAlice, ResolvedToBob. A refunded contract returns funds directly, no claim needed.".format(
            job_id, status, _status_name(status)
        )

    args = "role=user,action=claim," + _build_args([("job_id", job_id)])
    ok, parsed, err = _call_shader(args)
    if not ok:
        return "Error claiming from contract {}: {}".format(job_id, err)
    return "Claimed {} groth (payment {} + collateral {}) from contract {}. Funds will appear in wallet within the next block.".format(
        total, payment, collateral, job_id
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
    this server is configured for. On the production contract it is
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


@mcp.tool()
def worker_register(stake: int) -> str:
    """
    Post a worker reputation bond on the Idios contract.

    The bond is a slashable stake tied to your worker pubkey (the same key
    counterparties put in worker_pubkey when creating contracts with you).
    It signals skin in the game: if you lose a dispute, the bond is slashed
    and collected by the protocol treasury. Requesters can check your bond
    with view_worker_bond before trusting you with work.

    BEAM only (asset_id 0), any amount, in groth. 1 BEAM = 100,000,000
    groth. Registering twice halts; use view_worker_bond first if unsure.

    Args:
        stake: Bond amount in groth. BEAM only.

    Returns confirmation once the bond is on chain, or error message.
    """
    args = "role=user,action=worker_register," + _build_args([("stake", stake)])
    ok, parsed, err = _call_shader(args)
    if not ok:
        return "Error registering worker bond: {}".format(err)
    return "Worker bond registered: {} groth staked. Check it with view_worker_bond.".format(stake)


@mcp.tool()
def worker_deregister() -> str:
    """
    Start withdrawing your worker reputation bond.

    Deregistering starts a cooldown equal to the contract's arbitrator
    timeout (20160 blocks, about 14 days, on production; much shorter on
    test deployments). After the cooldown passes, call worker_reclaim to
    get the stake back. The cooldown exists so a worker cannot yank the
    bond the moment a dispute is coming.

    While deregistering you count as unbonded for new work, but any open
    dispute can still encumber and slash the bond until it is reclaimed.

    Returns confirmation, or error message.
    """
    args = "role=user,action=worker_deregister," + _build_args([])
    ok, parsed, err = _call_shader(args)
    if not ok:
        return "Error deregistering worker bond: {}".format(err)
    return "Worker bond deregistering. Reclaim with worker_reclaim after the cooldown (equal to the arbitrator timeout) has passed."


@mcp.tool()
def worker_reclaim() -> str:
    """
    Reclaim your worker reputation bond after deregistering.

    Only works once the cooldown (equal to the contract's arbitrator
    timeout) has passed since worker_deregister. The call halts on chain if:
    - the cooldown has not passed yet,
    - an open dispute currently encumbers the bond (wait for the dispute to
      resolve or be voided, check bond_encumbered in view_dispute), or
    - the bond was slashed. A slashed bond is gone forever; the treasury
      collects it. There is no reclaim path after a slash.

    Check view_worker_bond first: state must be 1 (deregistering) with zero
    encumbrances and the cooldown elapsed.

    Returns confirmation of the reclaim, or error message.
    """
    args = "role=user,action=worker_reclaim," + _build_args([])
    ok, parsed, err = _call_shader(args)
    if not ok:
        return "Error reclaiming worker bond: {}".format(err)
    return "Worker bond reclaimed. The stake will appear in your wallet within the next block."


@mcp.tool()
def view_worker_bond(worker_pk: str = "") -> str:
    """
    Check a worker's reputation bond on the Idios contract.

    With no argument, shows your own bond (derived from your wallet key).
    Pass a counterparty's worker pubkey to check theirs before creating a
    contract with them: a live bond means they have slashable stake behind
    their work.

    Fields returned:
    - stake: bonded amount in groth (BEAM).
    - bonded_at: block the bond was registered.
    - dereg_block: block deregistration started, 0 if not deregistering.
    - encumbrances: number of open disputes currently holding a lien on the
      bond. Reclaim halts while this is above zero.
    - state: 0 registered (active bond), 1 deregistering (cooldown running),
      2 gone (reclaimed), 3 slashed (lost a dispute, stake forfeited to the
      treasury, no recovery).

    Read only, needs no wallet funds.

    Args:
        worker_pk: Optional worker pubkey hex. Defaults to your own key.

    Returns the bond record as JSON with the state decoded, or an error
    message.
    """
    parts = []
    if worker_pk:
        parts.append(("worker_pk", worker_pk))
    args = "role=user,action=view_worker_bond," + _build_args(parts)
    ok, parsed, err = _call_shader(args)
    if not ok or parsed is None:
        return "Error viewing worker bond: {}".format(err)
    bond = parsed.get("worker_bond") or parsed.get("bond") or parsed
    state_names = {0: "registered", 1: "deregistering", 2: "gone", 3: "slashed"}
    try:
        state_int = int(bond.get("state", -1))
        bond["state_name"] = state_names.get(state_int, "unknown({})".format(state_int))
    except Exception:
        pass
    return json.dumps(bond, indent=2)


@mcp.tool()
def view_worker_reputation(worker_pk: str = "", payment: int = 0) -> str:
    """
    The worker card: everything this server knows about a worker before you
    hire them, plus a suggested collateral amount for a job.

    Combines two signals:
    1. The on chain bond (global, unfakeable): slashable stake the worker
       has locked behind their work. Losing an arbitrated dispute forfeits
       it, so a live bond is money where their mouth is.
    2. This server's own observed history (local, wash trading resistant):
       every contract THIS server has viewed involving the worker's key,
       bucketed into completions, lost disputes, abandoned jobs, and so on.
       It is deliberately NOT a global score: strangers cannot inflate what
       you personally observed.

    Pass a payment amount to also get a suggested collateral to demand:
    high for an unknown key, lower as bond coverage and clean observed
    history accumulate, full collateral (or a do not hire warning) for a
    slashed bond or observed bad history. The suggestion is a transparent
    heuristic with its reasoning spelled out, not a guarantee.

    Args:
        worker_pk: Worker pubkey hex. Defaults to your own key.
        payment: Optional job payment in groth. If above zero, a suggested
            collateral for that payment is included.

    Returns a JSON worker card, or an error message.
    """
    if not worker_pk:
        try:
            ok, parsed, err = _call_shader("role=user,action=get_key," + _build_args([]))
        except Exception as e:
            return "Error deriving own key: {}".format(e)
        if not ok or parsed is None:
            return "Error deriving own key: {}".format(err)
        key = parsed.get("key") or parsed
        worker_pk = key.get("pub_key", "")
        if not worker_pk:
            return "Error: could not derive own pubkey."

    card = {"worker_pk": worker_pk}

    try:
        bond_raw = view_worker_bond(worker_pk)
    except Exception as e:
        bond_raw = "bond lookup failed: {}".format(e)
    bond_state = -1
    bond_stake = 0
    try:
        bond = json.loads(bond_raw)
        bond_state = int(bond.get("state", -1))
        bond_stake = int(bond.get("stake", 0))
        card["bond"] = bond
    except (json.JSONDecodeError, ValueError):
        card["bond"] = "none found ({})".format(bond_raw.strip()[:80])

    stats = _reputation_stats(worker_pk, _ledger_load())
    card["observed_by_this_server"] = stats

    if payment > 0:
        amount, reasons = _suggest_collateral(payment, bond_state, bond_stake, stats)
        card["suggested_collateral"] = {
            "payment": payment,
            "suggested": amount,
            "reasoning": reasons,
        }

    return json.dumps(card, indent=2)


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
        globals()["_config_path"] = args.config
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

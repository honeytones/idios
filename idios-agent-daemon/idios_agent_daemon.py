#!/usr/bin/env python3
"""
Idios agent runtime daemon (MVP).

Watches a list of Idios jobs and automates the worker and client sides of the
state machine by shelling out to beam-wallet shader.

Worker role:
    status=Open          -> fire commit (collateral from config)
    status=Active        -> fire submit_delivery (pre-configured delivery_hash)
                            Mode A auto-settles to Closed at delivery, no claim.
    status=Settled       -> fire claim (Mode B approve path)
    status=ResolvedToBob -> fire claim (won dispute, payout is payment + collateral)
    status=Disputed      -> fire void_dispute once past the arbitrator timeout
    status=Voided        -> fire void_claim_node to reclaim collateral
    status=Cancelled     -> terminal; mutual cancel pays out in the cancel tx

Client role (all auto actions are config gated):
    status=Open/Active   -> fire refund after expiry (auto_refund_after_expiry)
    status=AwaitingApproval -> fire approve on hash match (auto_approve_on_hash_match)
    status=ResolvedToAlice  -> fire claim (won dispute, payout is payment + collateral)
    status=Disputed      -> fire void_dispute once past the arbitrator timeout
    status=Voided        -> fire void_claim_requester to reclaim payment
    status=Refunded      -> terminal; refund returns funds in its own tx.
    status=Cancelled     -> terminal; mutual cancel pays out in the cancel tx

Dispute resolution is arbitrators voting on chain (role=arbitrator,
action=vote via the CLI). Voting is deliberately not automated here: the
daemon never resolves disputes, it only waits, claims for the winner, or
voids a stale dispute past the timeout. The dispute fee goes to the voting
arbitrators, never to the winner. A Resolved job stays at status 6 or 7
forever; the winner_paid flag in view_dispute is the only paid signal.

Batch creation (optional):
    A top-level "batches" key in config lets an operator define N Mode B
    contracts to create in a single transaction before the poll loop starts.
    Each batch fires once. On confirmed success (shader ok + view_job confirms
    first job_id landed) the batch is marked submitted in durable state and
    never retried. If the tx fails for any reason the batch is NOT marked and
    will retry on next daemon start.

    NOTE: batch creation and ongoing job management are two manual steps.
    After a batch lands, add the resulting job_ids to the "jobs" list in config
    for the daemon to manage them through their lifecycle.

Run:
    python3 idios_agent_daemon.py /path/to/config.json

Daemon prompts once for the wallet password at startup and holds it in memory
for the lifetime of the process. The password is passed to each beam-wallet
subprocess via its --pass argument (visible in the process list while a call
runs) and is never written to disk.

The configured shader_app_file, beam_wallet_binary, wallet_path, node_addr, and
cid are taken from config.json. See the sample config alongside this file.
"""

import sys
import os
import json
import time
import logging
import getpass
import subprocess
import re
from pathlib import Path
from datetime import datetime

# Status integers from the contract, mirroring statusToText in ArbitratorPage.tsx.
STATUS_OPEN = 0
STATUS_ACTIVE = 1
STATUS_AWAITING_APPROVAL = 2
STATUS_DISPUTED = 3
STATUS_SETTLED = 4
STATUS_REFUNDED = 5
STATUS_RESOLVED_TO_ALICE = 6
STATUS_RESOLVED_TO_BOB = 7
STATUS_CLOSED = 8
STATUS_VOIDED = 9
STATUS_CANCELLED = 10

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

# Mode values are ASCII codes for 'A' and 'B', mirroring api.tsx.
MODE_A = 65
MODE_B = 66

DEFAULT_POLL_INTERVAL_SECONDS = 30
SHADER_TIMEOUT_SECONDS = 600

BATCH_MAX_COUNT = 50

# Required fields per spec entry in a batch definition.
BATCH_SPEC_REQUIRED_FIELDS = [
    "job_id",
    "subnet_id",
    "epoch",
    "expiry_block",
    "review_window_blocks",
    "payment",
    "dispute_fee",
    "asset_id",
    "node_pk",
]


def setup_logging(logfile_path):
    logger = logging.getLogger("idios-daemon")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)
    if logfile_path:
        file_handler = logging.FileHandler(logfile_path)
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    return logger


def load_config(path):
    with open(path, "r") as f:
        cfg = json.load(f)
    required = ["beam_wallet_binary", "shader_app_file", "wallet_path",
                "node_addr", "cid", "jobs"]
    missing = [k for k in required if k not in cfg]
    if missing:
        raise ValueError("config missing required keys: " + ", ".join(missing))
    if not isinstance(cfg["jobs"], list):
        raise ValueError("config.jobs must be a list")
    for j in cfg["jobs"]:
        for k in ("job_id", "role"):
            if k not in j:
                raise ValueError("each job needs job_id and role: " + str(j))
        if j["role"] not in ("worker", "client"):
            raise ValueError("role must be worker or client (arbitration is CLI voting, not automated): " + str(j))
    cfg.setdefault("poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS)

    # Validate batches if present.
    if "batches" in cfg:
        if not isinstance(cfg["batches"], list):
            raise ValueError("config.batches must be a list")
        for b in cfg["batches"]:
            if "batch_id" not in b:
                raise ValueError("each batch needs a batch_id: " + str(b))
            if not isinstance(b.get("specs"), list) or len(b["specs"]) == 0:
                raise ValueError("batch " + str(b.get("batch_id")) + ": specs must be a non-empty list")
            if len(b["specs"]) > BATCH_MAX_COUNT:
                raise ValueError(
                    "batch " + str(b.get("batch_id")) + ": specs count " +
                    str(len(b["specs"])) + " exceeds max " + str(BATCH_MAX_COUNT)
                )
            for i, spec in enumerate(b["specs"]):
                missing_fields = [f for f in BATCH_SPEC_REQUIRED_FIELDS if f not in spec]
                if missing_fields:
                    raise ValueError(
                        "batch " + str(b.get("batch_id")) + " spec[" + str(i) + "]: "
                        "missing fields: " + ", ".join(missing_fields)
                    )
                if int(spec.get("payment", 0)) <= 0:
                    raise ValueError(
                        "batch " + str(b.get("batch_id")) + " spec[" + str(i) + "]: "
                        "payment must be > 0"
                    )
                if int(spec.get("dispute_fee", 0)) <= 0:
                    raise ValueError(
                        "batch " + str(b.get("batch_id")) + " spec[" + str(i) + "]: "
                        "dispute_fee must be > 0"
                    )

    return cfg


def load_durable_state(state_path):
    if not os.path.exists(state_path):
        return {}
    try:
        with open(state_path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_durable_state(state_path, state):
    tmp = state_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, state_path)


def parse_shader_output(stdout_text):
    """
    beam-wallet prints shader output as:
        Shader output: "job": {"job_id": 11112, ...}
    or for get_key:
        Shader output: "key": {"pub_key": "..."}
    The dapp wraps it as '{' + raw + '}' and JSON.parses. Same trick here.
    Returns the parsed dict, or None if no shader output line found.
    """
    for line in stdout_text.splitlines():
        idx = line.find("Shader output:")
        if idx == -1:
            continue
        raw = line[idx + len("Shader output:"):].strip()
        if not raw:
            return None
        # raw looks like:   "job": {...}
        try:
            return json.loads("{" + raw + "}")
        except json.JSONDecodeError:
            # Some shader outputs may already be a full JSON object.
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"_raw": raw, "_parse_error": True}
    return None


def call_shader(cfg, password, shader_args, logger):
    """
    Subprocess beam-wallet with shader subcommand, password piped via stdin.
    Returns (returncode, stdout_text, stderr_text, parsed_output_dict_or_None).
    """
    cmd = [
        cfg["beam_wallet_binary"],
        "shader",
        "--pass=" + password,
        "--shader_app_file=" + cfg["shader_app_file"],
        "--shader_args=" + shader_args,
        "--node_addr=" + cfg["node_addr"],
        "--wallet_path=" + cfg["wallet_path"],
    ]
    wallet_cwd = os.path.dirname(cfg["beam_wallet_binary"])
    logger.info("shader call: %s", shader_args)
    try:
        proc = subprocess.run(
            cmd,
            input=b"y\n",
            capture_output=True,
            timeout=SHADER_TIMEOUT_SECONDS,
            cwd=wallet_cwd,
        )
    except subprocess.TimeoutExpired:
        logger.error("shader call timed out after %ss: %s", SHADER_TIMEOUT_SECONDS, shader_args)
        return (-1, "", "TIMEOUT", None)
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    parsed = parse_shader_output(stdout)
    # beam-wallet's return code is unreliable; it often exits 1 even on
    # successful shader execution. Success is signalled by either parseable
    # shader output (read-only calls) or the "Transaction completed" marker
    # (state-changing calls, which emit no shader output object). Trust those,
    # never the rc. Same logic as the MCP server.
    if parsed is not None or "Transaction completed" in stdout:
        effective_rc = 0
    else:
        effective_rc = proc.returncode
    if effective_rc != 0:
        logger.error("shader call failed rc=%s. stdout tail: %s | stderr tail: %s",
                     proc.returncode, stdout[-800:], stderr[-300:])
    return (effective_rc, stdout, stderr, parsed)


def build_args(cfg, parts):
    """parts is a list of (k, v) tuples. Order matters for readability only."""
    pairs = ["cid=" + cfg["cid"]]
    for k, v in parts:
        pairs.append("{}={}".format(k, v))
    return ",".join(pairs)


def shader_view_job(cfg, password, job_id, logger):
    args = "role=manager,action=view_job," + build_args(cfg, [("job_id", job_id)])
    rc, _, _, parsed = call_shader(cfg, password, args, logger)
    if rc != 0 or parsed is None:
        return None
    return parsed.get("job") or parsed


def shader_view_params(cfg, password, logger):
    """Read the contract params (arbitrator_pk, treasury_pk,
    default_review_window, arbitrator_timeout_blocks) via the manager view
    action. Returns the params dict, or None."""
    args = "role=manager,action=view," + build_args(cfg, [])
    rc, _, _, parsed = call_shader(cfg, password, args, logger)
    if rc != 0 or parsed is None:
        return None
    return parsed.get("params") or parsed


def get_current_height(cfg, password, logger):
    """Read the current block height via beam-wallet info, parsing the
    'Current height' line. Returns int height, or None on any failure."""
    cmd = [
        cfg["beam_wallet_binary"], "info",
        "--node_addr=" + cfg["node_addr"],
        "--wallet_path=" + cfg["wallet_path"],
        "--pass=" + password,
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=os.path.dirname(cfg["beam_wallet_binary"]),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as e:
        logger.warning("could not read chain height: %s", e)
        return None
    out = proc.stdout + proc.stderr
    # The wallet DB's "Current height" can be DAYS stale until the wallet
    # syncs; the sync log lines carry the node tip. Collect every height
    # signal and take the maximum (same fix as the MCP server).
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
        return max(heights)
    logger.warning("could not parse any height signal from wallet info output")
    return None


def shader_commit(cfg, password, job_id, collateral, asset_id, logger):
    args = "role=user,action=commit," + build_args(cfg, [
        ("job_id", job_id),
        ("collateral", collateral),
        ("asset_id", asset_id),
    ])
    rc, _, _, _ = call_shader(cfg, password, args, logger)
    return rc == 0


def shader_submit_delivery(cfg, password, job_id, delivery_hash, mode, payment, collateral, asset_id, logger):
    args = "role=user,action=submit_delivery," + build_args(cfg, [
        ("job_id", job_id),
        ("delivery_hash", delivery_hash),
        ("mode", mode),
        ("payment", payment),
        ("collateral", collateral),
        ("asset_id", asset_id),
    ])
    rc, _, _, _ = call_shader(cfg, password, args, logger)
    return rc == 0


def shader_claim(cfg, password, job_id, logger):
    """The app shader reads payment, collateral, dispute_fee and asset_id
    from chain; only job_id is needed."""
    args = "role=user,action=claim," + build_args(cfg, [("job_id", job_id)])
    rc, _, _, _ = call_shader(cfg, password, args, logger)
    return rc == 0


def shader_approve(cfg, password, job_id, logger):
    args = "role=user,action=approve," + build_args(cfg, [("job_id", job_id)])
    rc, _, _, _ = call_shader(cfg, password, args, logger)
    return rc == 0


def shader_refund(cfg, password, job_id, logger):
    """v4 refund: the shader reads payment and asset_id from chain, only
    job_id is needed. Funds return in the refund tx itself, no claim after.
    On the Active path the worker's collateral is forfeited to the treasury."""
    args = "role=user,action=refund," + build_args(cfg, [("job_id", job_id)])
    rc, _, _, _ = call_shader(cfg, password, args, logger)
    return rc == 0


def shader_view_dispute(cfg, password, job_id, logger):
    """Read the on chain dispute record for a job. Returns the parsed dict
    (frozen_n, threshold, vc_alice, vc_bob, resolution, winner_paid,
    fee_share, fee_remainder, remainder_swept, bond_encumbered) or None.
    winner_paid is the ONLY signal a Resolved (6/7) job was paid out, since
    the status stays 6 or 7 forever after the winner claims."""
    args = "role=manager,action=view_dispute," + build_args(cfg, [("job_id", job_id)])
    rc, _, _, parsed = call_shader(cfg, password, args, logger)
    if rc != 0 or parsed is None:
        return None
    return parsed.get("dispute") or parsed


def shader_view_worker_bond(cfg, password, worker_pk, logger):
    """Read a worker's on chain reputation bond. Returns the bond dict, or
    None if the worker has no bond record (or the read failed)."""
    args = "role=user,action=view_worker_bond," + build_args(cfg, [
        ("worker_pk", worker_pk),
    ])
    rc, _, _, parsed = call_shader(cfg, password, args, logger)
    if rc != 0 or parsed is None:
        return None
    return parsed.get("worker_bond") or parsed.get("bond") or parsed


BOND_STATE_NAMES = {0: "registered", 1: "deregistering", 2: "gone", 3: "slashed"}


def evaluate_worker_bond(bond, min_stake):
    """Pure decision: given a bond dict (or None) and a configured floor,
    return (acceptable, description). acceptable is against min_stake only;
    a slashed bond is never acceptable when a floor is set."""
    if not bond:
        desc = "no bond on chain"
        return (min_stake <= 0, desc)
    try:
        state = int(bond.get("state", -1))
        stake = int(bond.get("stake", 0))
    except (TypeError, ValueError):
        return (min_stake <= 0, "unreadable bond record")
    desc = "bond stake={} groth, state={}".format(
        stake, BOND_STATE_NAMES.get(state, "unknown({})".format(state)))
    if min_stake <= 0:
        return (True, desc)
    if state == 3:
        return (False, desc + " (SLASHED: lost an arbitrated dispute)")
    if state != 0:
        return (False, desc + " (bond not live)")
    return (stake >= int(min_stake), desc)


def log_worker_card(cfg, password, worker_pk, logger, context):
    """Advisory: log what is known about a worker's bond. Never raises."""
    try:
        bond = shader_view_worker_bond(cfg, password, worker_pk, logger)
        _, desc = evaluate_worker_bond(bond, 0)
        logger.info("worker card (%s): pk=%s...: %s", context, str(worker_pk)[:16], desc)
    except Exception as e:
        logger.warning("worker card (%s): lookup failed: %s", context, e)


def shader_void_dispute(cfg, password, job_id, logger):
    """Flip a Disputed job the arbitrator never resolved into Voided.
    Permissionless trigger; gated on-chain by the arbitrator timeout."""
    args = "role=user,action=void_dispute," + build_args(cfg, [
        ("job_id", job_id),
    ])
    rc, _, _, _ = call_shader(cfg, password, args, logger)
    return rc == 0


def shader_void_claim_requester(cfg, password, job_id, logger):
    """Requester reclaims their payment from a voided dispute."""
    args = "role=user,action=void_claim_requester," + build_args(cfg, [
        ("job_id", job_id),
    ])
    rc, _, _, _ = call_shader(cfg, password, args, logger)
    return rc == 0


def shader_void_claim_node(cfg, password, job_id, logger):
    """Node reclaims their collateral from a voided dispute."""
    args = "role=user,action=void_claim_node," + build_args(cfg, [
        ("job_id", job_id),
    ])
    rc, _, _, _ = call_shader(cfg, password, args, logger)
    return rc == 0


def shader_treasury_sweep(cfg, password, job_id, logger):
    """Treasury collects forfeited funds: collateral on a Refunded job,
    or the unawardable dispute_fee on a Voided job."""
    args = "role=treasury,action=sweep," + build_args(cfg, [
        ("job_id", job_id),
    ])
    rc, _, _, _ = call_shader(cfg, password, args, logger)
    return rc == 0


def shader_batch_create_b(cfg, password, specs, logger):
    """
    Fire one batch_create_b transaction creating len(specs) Mode B contracts.

    Builds the indexed arg format the shader expects:
        batch_count=N,job_id_0=...,subnet_id_0=..., ...,job_id_1=..., ...

    The shader uses Utils::MakeFieldIndex<50>("field_") which produces keys like
    job_id_0, job_id_1, etc. (trailing underscore is part of the prefix in the
    shader source, the produced key does NOT have a double underscore: the
    prefix "job_id_" + index "0" = "job_id_0").

    Returns True if the shader call succeeded (parsed output present), False otherwise.
    Caller is responsible for confirming on chain before marking state.
    """
    batch_count = len(specs)
    parts = [("batch_count", batch_count)]
    for i, spec in enumerate(specs):
        parts.append(("job_id_" + str(i),               spec["job_id"]))
        parts.append(("subnet_id_" + str(i),            spec["subnet_id"]))
        parts.append(("epoch_" + str(i),                spec["epoch"]))
        parts.append(("expiry_block_" + str(i),         spec["expiry_block"]))
        parts.append(("review_window_blocks_" + str(i), spec["review_window_blocks"]))
        parts.append(("payment_" + str(i),              spec["payment"]))
        parts.append(("dispute_fee_" + str(i),          spec["dispute_fee"]))
        parts.append(("asset_id_" + str(i),             spec["asset_id"]))
        parts.append(("node_pk_" + str(i),              spec["node_pk"]))
    args = "role=user,action=batch_create_b," + build_args(cfg, parts)
    # Log the full args string before firing so the operator can eyeball the
    # key format (job_id_0, payment_0, etc.) and catch any format mismatch
    # before a real tx goes out.
    logger.info("batch_create_b args: %s", args)
    rc, _, _, _ = call_shader(cfg, password, args, logger)
    return rc == 0


def run_batches(cfg, password, state, state_path, logger):
    """
    Process all batches defined in config. Called once before the poll loop.

    Each batch fires at most once. A batch is only marked submitted after:
      1. shader_batch_create_b returns True (shader call produced output)
      2. view_job on the first job_id in the batch returns a valid job

    If either step fails the batch is NOT marked and will retry on next daemon
    start. This is the correct behaviour: a failed or unconfirmed tx should
    always be retryable.

    After a batch lands the operator must manually add the resulting job_ids to
    the "jobs" list in config for the daemon to manage them through their
    lifecycle. Batch creation and ongoing job management are intentionally two
    separate manual steps.
    """
    batches = cfg.get("batches", [])
    if not batches:
        return

    logger.info("batch processing: %d batch(es) defined in config", len(batches))

    for batch_cfg in batches:
        batch_id = batch_cfg["batch_id"]
        state_key = "batch_submitted_" + batch_id
        specs = batch_cfg["specs"]

        if state.get(state_key):
            logger.info("batch %s: already submitted (durable state), skipping", batch_id)
            continue

        total_payment = sum(int(s["payment"]) for s in specs)
        job_ids = [s["job_id"] for s in specs]

        logger.info(
            "batch %s: preparing batch_create_b, count=%d, job_ids=%s, "
            "total_payment=%d groth (wallet must have this available)",
            batch_id, len(specs), job_ids, total_payment
        )

        # Worker card pre flight: one bond check per distinct worker in the
        # batch. Advisory by default; if min_worker_bond_stake is set in
        # config, refuse to fire a batch containing any worker below the
        # floor (escrow graduation: only hire bonded workers).
        min_stake = int(cfg.get("min_worker_bond_stake", 0))
        distinct_pks = []
        for spec in specs:
            pk = spec["node_pk"]
            if pk not in distinct_pks:
                distinct_pks.append(pk)
        blocked = []
        for pk in distinct_pks:
            bond = shader_view_worker_bond(cfg, password, pk, logger)
            acceptable, desc = evaluate_worker_bond(bond, min_stake)
            logger.info("batch %s worker card: pk=%s...: %s", batch_id, str(pk)[:16], desc)
            if not acceptable:
                blocked.append((pk, desc))
        if blocked:
            logger.error(
                "batch %s: NOT firing. min_worker_bond_stake=%d and %d worker(s) "
                "fall below it or hold a dead bond: %s. Batch will re-evaluate "
                "on next daemon start.",
                batch_id, min_stake, len(blocked),
                "; ".join("{}... ({})".format(str(p)[:16], d) for p, d in blocked))
            continue

        ok = shader_batch_create_b(cfg, password, specs, logger)
        if not ok:
            logger.error(
                "batch %s: shader call failed or produced no output. "
                "Batch NOT marked submitted. Will retry on next daemon start.",
                batch_id
            )
            continue

        # Shader returned output. Now confirm the first job_id actually landed
        # on chain before marking the batch as submitted.
        first_job_id = specs[0]["job_id"]
        logger.info(
            "batch %s: shader call ok. Confirming first job_id %s landed on chain...",
            batch_id, first_job_id
        )

        # Poll view_job until the contract lands or we give up.
        # Blocks can take 20+ seconds. Poll every 15s for up to 75s (5 attempts)
        # before concluding it didn't land. This avoids a false not-found on a
        # successful batch that just hasn't confirmed yet.
        CONFIRM_POLL_INTERVAL = 15
        CONFIRM_POLL_ATTEMPTS = 5
        confirmed_job = None
        for attempt in range(1, CONFIRM_POLL_ATTEMPTS + 1):
            logger.info(
                "batch %s: waiting %ss before view_job attempt %d/%d for job_id %s...",
                batch_id, CONFIRM_POLL_INTERVAL, attempt, CONFIRM_POLL_ATTEMPTS, first_job_id
            )
            time.sleep(CONFIRM_POLL_INTERVAL)
            confirmed_job = shader_view_job(cfg, password, first_job_id, logger)
            if confirmed_job:
                logger.info(
                    "batch %s: job_id %s confirmed on chain after attempt %d",
                    batch_id, first_job_id, attempt
                )
                break
            logger.info(
                "batch %s: view_job attempt %d returned no data, will retry",
                batch_id, attempt
            )

        if not confirmed_job:
            logger.error(
                "batch %s: view_job on job_id %s returned no data after %d attempts (~%ds). "
                "Tx may not have landed yet. Batch NOT marked submitted. "
                "Check chain state via dapp. If contracts exist, set '%s': true "
                "in state file manually to prevent resubmit.",
                batch_id, first_job_id, CONFIRM_POLL_ATTEMPTS,
                CONFIRM_POLL_INTERVAL * CONFIRM_POLL_ATTEMPTS, state_key
            )
            continue

        # Confirmed on chain.
        state[state_key] = True
        save_durable_state(state_path, state)
        logger.info(
            "batch %s: confirmed on chain (job_id %s status=%s). "
            "Marked submitted. Add job_ids %s to the 'jobs' list in config "
            "to manage them through their lifecycle.",
            batch_id, first_job_id,
            STATUS_NAMES.get(int(confirmed_job.get("status", -1)), "Unknown"),
            job_ids
        )


def status_name(s):
    try:
        return STATUS_NAMES.get(int(s), "Unknown(" + str(s) + ")")
    except Exception:
        return "Unknown(" + str(s) + ")"


def maybe_void_stale_dispute(cfg, password, job_id, job, job_state,
                             chain_height, arbitrator_timeout, logger):
    """Fire void_dispute on a Disputed job once strictly past
    dispute_filed_block + arbitrator_timeout_blocks. Permissionless on chain,
    so either role can trigger it. No-op if height or timeout is unknown."""
    if job_state.get("void_fired"):
        return
    if not arbitrator_timeout or chain_height is None:
        logger.info("job %s: Disputed, waiting for the arbitrator vote "
                    "(auto void unavailable: no chain height or timeout).", job_id)
        return
    filed = int(job.get("dispute_filed_block", 0))
    if filed == 0:
        logger.warning("job %s: Disputed but dispute_filed_block is 0, not auto-voiding.", job_id)
        return
    deadline = filed + int(arbitrator_timeout)
    if chain_height <= deadline:
        logger.info("job %s: Disputed, arbitrators can vote until block %s "
                    "(current %s). Waiting.", job_id, deadline, chain_height)
        return
    logger.info("job %s: dispute stale (deadline block %s, current %s), "
                "firing void_dispute", job_id, deadline, chain_height)
    if shader_void_dispute(cfg, password, job_id, logger):
        job_state["void_fired"] = True
        logger.info("job %s: void_dispute fired ok", job_id)
    else:
        logger.error("job %s: void_dispute failed", job_id)


def handle_worker_job(cfg, password, job_cfg, state, logger,
                      chain_height=None, arbitrator_timeout=0):
    """
    Mode A worker MVP: commit -> submit_delivery -> claim if Settled.
    Idempotent. Each transition is fired at most once thanks to durable state.
    """
    job_id = job_cfg["job_id"]
    # Namespace state by role: a single job_id can appear twice in config
    # (once as worker, once as client) for a self-dealing setup, and they
    # must not share a state object or one role's flags block the other's.
    job_state_key = "worker:" + str(job_id)
    job_state = state.setdefault(job_state_key, {
        "last_status": None,
        "commit_fired": False,
        "submit_fired": False,
        "claim_fired": False,
    })

    job = shader_view_job(cfg, password, job_id, logger)
    if not job:
        logger.warning("job %s: view_job failed or no data", job_id)
        return

    status = int(job.get("status", -1))
    payment = int(job.get("payment", 0))
    collateral = int(job.get("collateral", 0))
    mode = int(job.get("mode", MODE_A))
    asset_id = int(job.get("asset_id", 0))

    if job_state.get("last_status") != status:
        logger.info("job %s: status -> %s (%s)", job_id, status, status_name(status))
        job_state["last_status"] = status

    if status == STATUS_OPEN and not job_state.get("commit_fired"):
        expected_collateral = job_cfg.get("expected_collateral")
        if expected_collateral is None or int(expected_collateral) <= 0:
            logger.error("job %s: expected_collateral missing or zero in config. NOT firing commit.",
                         job_id)
            return
        commit_amount = int(expected_collateral)
        logger.info("job %s: firing commit, collateral=%s", job_id, commit_amount)
        if shader_commit(cfg, password, job_id, commit_amount, asset_id, logger):
            job_state["commit_fired"] = True
            logger.info("job %s: commit fired ok", job_id)
        else:
            logger.error("job %s: commit failed", job_id)
        return

    if status == STATUS_ACTIVE and not job_state.get("submit_fired"):
        delivery_hash = job_cfg.get("delivery_hash")
        if not delivery_hash:
            logger.error("job %s: no delivery_hash in config, NOT firing submit_delivery", job_id)
            return
        logger.info("job %s: firing submit_delivery, mode=%s delivery_hash=%s",
                    job_id, mode, delivery_hash)
        if shader_submit_delivery(cfg, password, job_id, delivery_hash, mode, payment, collateral, asset_id, logger):
            job_state["submit_fired"] = True
            logger.info("job %s: submit_delivery fired ok", job_id)
        else:
            logger.error("job %s: submit_delivery failed", job_id)
        return

    if status == STATUS_SETTLED and not job_state.get("claim_fired"):
        total = payment + collateral
        logger.info("job %s: firing claim, total=%s (payment %s + collateral %s)",
                    job_id, total, payment, collateral)
        if shader_claim(cfg, password, job_id, logger):
            job_state["claim_fired"] = True
            logger.info("job %s: claim fired ok, funds should be in wallet within next block",
                        job_id)
        else:
            logger.error("job %s: claim failed", job_id)
        return

    if status == STATUS_RESOLVED_TO_BOB and not job_state.get("claim_fired"):
        # A Resolved job stays at status 7 forever, even after the payout.
        # winner_paid from view_dispute is the only paid signal, so check it
        # before firing a claim that would halt on chain (protects against a
        # lost state file or a claim made outside this daemon).
        dispute = shader_view_dispute(cfg, password, job_id, logger)
        if dispute is not None and int(dispute.get("winner_paid", 0)) == 1:
            job_state["claim_fired"] = True
            logger.info("job %s: dispute payout already claimed (winner_paid=1), "
                        "marking done", job_id)
            return
        total = payment + collateral
        logger.info("job %s: dispute resolved to worker, firing claim, total=%s "
                    "(payment %s + collateral %s, the dispute fee goes to the "
                    "voting arbitrators)", job_id, total, payment, collateral)
        if shader_claim(cfg, password, job_id, logger):
            job_state["claim_fired"] = True
            logger.info("job %s: claim fired ok", job_id)
        else:
            logger.error("job %s: claim failed", job_id)
        return

    if status == STATUS_DISPUTED:
        maybe_void_stale_dispute(cfg, password, job_id, job, job_state,
                                 chain_height, arbitrator_timeout, logger)
        return

    if status == STATUS_VOIDED and not job_state.get("void_claim_fired"):
        if collateral <= 0:
            # Nothing to reclaim (already claimed, or never committed).
            job_state["void_claim_fired"] = True
            return
        logger.info("job %s: Voided, firing void_claim_node to reclaim "
                    "collateral %s", job_id, collateral)
        if shader_void_claim_node(cfg, password, job_id, logger):
            job_state["void_claim_fired"] = True
            logger.info("job %s: void_claim_node fired ok", job_id)
        else:
            logger.error("job %s: void_claim_node failed", job_id)
        return

    if status in (STATUS_CLOSED, STATUS_REFUNDED, STATUS_RESOLVED_TO_ALICE, STATUS_CANCELLED):
        return


def handle_client_job(cfg, password, job_cfg, state, logger,
                      chain_height=None, arbitrator_timeout=0):
    """
    Client role state machine. Default behaviour: manual approval. Operator must
    set "auto_approve_on_hash_match": true and provide "expected_delivery_hash"
    in config to enable auto-approve. Set "auto_refund_after_expiry": true to
    auto-refund an expired Open or Active job (on the Active path the worker's
    collateral is forfeited to the treasury, so only enable this if that is the
    intended remedy for non-delivery). Fires claim on ResolvedToAlice, voids a
    stale dispute, and reclaims the payment from a Voided job. v4: Refunded
    needs no claim, funds return in the refund tx itself.
    """
    job_id = job_cfg["job_id"]
    job_state_key = "client:" + str(job_id)
    job_state = state.setdefault(job_state_key, {
        "last_status": None,
        "approve_fired": False,
        "refund_fired": False,
        "claim_fired": False,
    })

    job = shader_view_job(cfg, password, job_id, logger)
    if not job:
        logger.warning("job %s: view_job failed or no data", job_id)
        return

    status = int(job.get("status", -1))
    payment = int(job.get("payment", 0))
    collateral = int(job.get("collateral", 0))
    expiry_block = int(job.get("expiry_block", 0))
    delivery_hash = job.get("delivery_hash", "")
    asset_id = int(job.get("asset_id", 0))
    mode = int(job.get("mode", MODE_A))

    if job_state.get("last_status") != status:
        logger.info("job %s: status -> %s (%s)", job_id, status, status_name(status))
        job_state["last_status"] = status

    if not job_state.get("worker_card_logged"):
        pk = job.get("node_pk")
        if pk:
            log_worker_card(cfg, password, pk, logger, "client job {}".format(job_id))
        job_state["worker_card_logged"] = True

    if status in (STATUS_OPEN, STATUS_ACTIVE) and not job_state.get("refund_fired"):
        if not job_cfg.get("auto_refund_after_expiry", False):
            return
        if chain_height is None:
            logger.info("job %s: auto_refund set but chain height unavailable "
                        "this cycle, will retry.", job_id)
            return
        if chain_height <= expiry_block:
            return
        logger.info("job %s: expired at block %s (current %s), firing refund%s",
                    job_id, expiry_block, chain_height,
                    " (worker collateral forfeits to treasury)" if status == STATUS_ACTIVE else "")
        if shader_refund(cfg, password, job_id, logger):
            job_state["refund_fired"] = True
            logger.info("job %s: refund fired ok, payment returns in the refund tx", job_id)
        else:
            logger.error("job %s: refund failed", job_id)
        return

    if status == STATUS_AWAITING_APPROVAL and not job_state.get("approve_fired"):
        if not job_cfg.get("auto_approve_on_hash_match", False):
            logger.info("job %s: AwaitingApproval, manual approval required. "
                        "Set auto_approve_on_hash_match in config to auto-approve.", job_id)
            return
        expected = job_cfg.get("expected_delivery_hash")
        if not expected:
            logger.error("job %s: auto_approve_on_hash_match set but no "
                         "expected_delivery_hash in config. NOT approving.", job_id)
            return
        if str(expected).lower() != str(delivery_hash).lower():
            logger.warning("job %s: delivery_hash mismatch, chain=%s expected=%s. "
                           "NOT auto-approving. Operator should review.",
                           job_id, delivery_hash, expected)
            return
        logger.info("job %s: delivery_hash matches expected, firing approve", job_id)
        if shader_approve(cfg, password, job_id, logger):
            job_state["approve_fired"] = True
            logger.info("job %s: approve fired ok", job_id)
        else:
            logger.error("job %s: approve failed", job_id)
        return

    if status == STATUS_RESOLVED_TO_ALICE and not job_state.get("claim_fired"):
        # Status stays 6 forever after the payout; winner_paid is the only
        # paid signal. Check it before firing a claim that would halt on
        # chain (protects against a lost state file or an external claim).
        dispute = shader_view_dispute(cfg, password, job_id, logger)
        if dispute is not None and int(dispute.get("winner_paid", 0)) == 1:
            job_state["claim_fired"] = True
            logger.info("job %s: dispute payout already claimed (winner_paid=1), "
                        "marking done", job_id)
            return
        total = payment + collateral
        logger.info("job %s: dispute resolved to client, firing claim, total=%s "
                    "(payment %s + collateral %s, the dispute fee goes to the "
                    "voting arbitrators)", job_id, total, payment, collateral)
        if shader_claim(cfg, password, job_id, logger):
            job_state["claim_fired"] = True
            logger.info("job %s: claim fired ok", job_id)
        else:
            logger.error("job %s: claim failed", job_id)
        return

    if status == STATUS_DISPUTED:
        maybe_void_stale_dispute(cfg, password, job_id, job, job_state,
                                 chain_height, arbitrator_timeout, logger)
        return

    if status == STATUS_VOIDED and not job_state.get("void_claim_fired"):
        if payment <= 0:
            job_state["void_claim_fired"] = True
            return
        logger.info("job %s: Voided, firing void_claim_requester to reclaim "
                    "payment %s", job_id, payment)
        if shader_void_claim_requester(cfg, password, job_id, logger):
            job_state["void_claim_fired"] = True
            logger.info("job %s: void_claim_requester fired ok", job_id)
        else:
            logger.error("job %s: void_claim_requester failed", job_id)
        return

    # v4: Refund returns the payment in the refund transaction itself, and
    # Claim halts on Refunded. Refunded is terminal for the client.
    if status in (STATUS_CLOSED, STATUS_REFUNDED, STATUS_RESOLVED_TO_BOB, STATUS_SETTLED, STATUS_CANCELLED):
        return


def main():
    if len(sys.argv) != 2:
        print("usage: idios_agent_daemon.py <config.json>", file=sys.stderr)
        sys.exit(2)

    config_path = sys.argv[1]
    try:
        cfg = load_config(config_path)
    except Exception as e:
        print("failed to load config: " + str(e), file=sys.stderr)
        sys.exit(2)

    config_dir = os.path.dirname(os.path.abspath(config_path))
    logfile_path = cfg.get("log_file") or os.path.join(config_dir, "idios-daemon.log")
    state_path = cfg.get("state_file") or os.path.join(config_dir, "jobs-state.json")
    logger = setup_logging(logfile_path)

    logger.info("idios agent daemon starting")
    logger.info("config: %s", config_path)
    logger.info("state file: %s", state_path)
    logger.info("log file: %s", logfile_path)
    logger.info("beam-wallet binary: %s", cfg["beam_wallet_binary"])
    logger.info("shader app file: %s", cfg["shader_app_file"])
    logger.info("wallet path: %s", cfg["wallet_path"])
    logger.info("node addr: %s", cfg["node_addr"])
    logger.info("cid: %s", cfg["cid"])
    logger.info("poll interval: %ss", cfg["poll_interval_seconds"])
    logger.info("jobs configured: %s", len(cfg["jobs"]))
    for j in cfg["jobs"]:
        logger.info("  job %s role=%s", j["job_id"], j["role"])
    batches = cfg.get("batches", [])
    if batches:
        logger.info("batches configured: %s", len(batches))
        for b in batches:
            logger.info("  batch %s specs=%s", b["batch_id"], len(b["specs"]))

    for p in (cfg["beam_wallet_binary"], cfg["shader_app_file"], cfg["wallet_path"]):
        if not os.path.exists(p):
            logger.error("path does not exist: %s", p)
            sys.exit(2)

    try:
        password = getpass.getpass("Wallet password: ")
    except (KeyboardInterrupt, EOFError):
        logger.info("daemon cancelled at password prompt")
        sys.exit(0)

    state = load_durable_state(state_path)

    # Read arbitrator_timeout_blocks once from chain. Needed for the auto
    # void_dispute trigger; if unreadable, the daemon still runs but stale
    # disputes must be voided manually.
    params = shader_view_params(cfg, password, logger)
    arbitrator_timeout = 0
    if params:
        try:
            arbitrator_timeout = int(params.get("arbitrator_timeout_blocks", 0))
        except (TypeError, ValueError):
            arbitrator_timeout = 0
    if arbitrator_timeout:
        logger.info("arbitrator_timeout_blocks: %s", arbitrator_timeout)
    else:
        logger.warning("could not read arbitrator_timeout_blocks from chain; "
                       "auto void_dispute disabled, void manually if needed")

    # Process batches once before entering the poll loop.
    run_batches(cfg, password, state, state_path, logger)

    logger.info("daemon ready, starting poll loop. Ctrl-C to stop.")
    try:
        while True:
            cycle_start = time.time()
            # One height read per cycle, shared by all jobs. Used by the
            # auto refund and auto void triggers; None just disables them
            # for this cycle.
            chain_height = get_current_height(cfg, password, logger)
            for job_cfg in cfg["jobs"]:
                try:
                    role = job_cfg["role"]
                    if role == "worker":
                        handle_worker_job(cfg, password, job_cfg, state, logger,
                                          chain_height, arbitrator_timeout)
                    elif role == "client":
                        handle_client_job(cfg, password, job_cfg, state, logger,
                                          chain_height, arbitrator_timeout)
                except Exception as e:
                    logger.exception("error handling job %s: %s", job_cfg.get("job_id"), e)
            save_durable_state(state_path, state)
            elapsed = time.time() - cycle_start
            sleep_for = max(1, int(cfg["poll_interval_seconds"]) - int(elapsed))
            time.sleep(sleep_for)
    except KeyboardInterrupt:
        logger.info("daemon stopped by user")
        save_durable_state(state_path, state)
        sys.exit(0)


if __name__ == "__main__":
    main()

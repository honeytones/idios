#!/usr/bin/env python3
"""
Idios agent runtime daemon (MVP).

Watches a list of Idios jobs and automates the worker side of the state machine
by shelling out to beam-wallet shader. Mode A worker loop only in this MVP:

    status=Open       -> fire commit (collateral)
    status=Active     -> fire submit_delivery (with pre-configured delivery_hash)
    status=Settled    -> fire claim

Client and arbitrator roles are not handled by this MVP. Disputed and other
terminal states are logged and left alone.

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
for the lifetime of the process. Password is piped to each beam-wallet
subprocess via stdin, never written to disk, never passed as a CLI arg.

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
        if j["role"] not in ("worker", "client", "arbitrator"):
            raise ValueError("role must be worker, client, or arbitrator: " + str(j))
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
    # successful shader execution. Trust the presence of parsed output instead.
    # Treat as failure only if we got no shader output AND rc != 0.
    effective_rc = 0 if parsed is not None else proc.returncode
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


def shader_claim(cfg, password, job_id, total, asset_id, logger):
    args = "role=user,action=claim," + build_args(cfg, [
        ("job_id", job_id),
        ("total", total),
        ("asset_id", asset_id),
    ])
    rc, _, _, _ = call_shader(cfg, password, args, logger)
    return rc == 0


def shader_approve(cfg, password, job_id, logger):
    args = "role=user,action=approve," + build_args(cfg, [("job_id", job_id)])
    rc, _, _, _ = call_shader(cfg, password, args, logger)
    return rc == 0


def shader_refund(cfg, password, job_id, payment, collateral, asset_id, logger):
    args = "role=user,action=refund," + build_args(cfg, [
        ("job_id", job_id),
        ("payment", payment),
        ("collateral", collateral),
        ("asset_id", asset_id),
    ])
    rc, _, _, _ = call_shader(cfg, password, args, logger)
    return rc == 0


def shader_resolve_alice(cfg, password, job_id, total, asset_id, logger):
    args = "role=arbitrator,action=resolve_alice," + build_args(cfg, [
        ("job_id", job_id),
        ("total", total),
        ("asset_id", asset_id),
    ])
    rc, _, _, _ = call_shader(cfg, password, args, logger)
    return rc == 0


def shader_resolve_bob(cfg, password, job_id, total, asset_id, logger):
    args = "role=arbitrator,action=resolve_bob," + build_args(cfg, [
        ("job_id", job_id),
        ("total", total),
        ("asset_id", asset_id),
    ])
    rc, _, _, _ = call_shader(cfg, password, args, logger)
    return rc == 0


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
            "batch %s: firing batch_create_b, count=%d, job_ids=%s, "
            "total_payment=%d groth (wallet must have this available)",
            batch_id, len(specs), job_ids, total_payment
        )

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


def handle_worker_job(cfg, password, job_cfg, state, logger):
    """
    Mode A worker MVP: commit -> submit_delivery -> claim if Settled.
    Idempotent. Each transition is fired at most once thanks to durable state.
    """
    job_id = job_cfg["job_id"]
    job_state_key = str(job_id)
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
        if shader_claim(cfg, password, job_id, total, asset_id, logger):
            job_state["claim_fired"] = True
            logger.info("job %s: claim fired ok, funds should be in wallet within next block",
                        job_id)
        else:
            logger.error("job %s: claim failed", job_id)
        return

    if status == STATUS_RESOLVED_TO_BOB and not job_state.get("claim_fired"):
        total = payment + collateral + int(job.get("dispute_fee", 0))
        logger.info("job %s: dispute resolved to worker, firing claim, total=%s", job_id, total)
        if shader_claim(cfg, password, job_id, total, asset_id, logger):
            job_state["claim_fired"] = True
            logger.info("job %s: claim fired ok", job_id)
        else:
            logger.error("job %s: claim failed", job_id)
        return

    if status == STATUS_DISPUTED:
        logger.info("job %s: Disputed, waiting for arbitrator. Daemon takes no action.", job_id)
        return

    if status in (STATUS_CLOSED, STATUS_REFUNDED, STATUS_RESOLVED_TO_ALICE):
        return


def handle_client_job(cfg, password, job_cfg, state, logger):
    """
    Client role state machine. Default behaviour: manual approval. Operator must
    set "auto_approve_on_hash_match": true and provide "expected_delivery_hash"
    in config to enable auto-approve. Auto-fires claim on any terminal state
    that returns funds (ResolvedToAlice, Refunded).
    """
    job_id = job_cfg["job_id"]
    job_state_key = str(job_id)
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
    dispute_fee = int(job.get("dispute_fee", 0))
    expiry_block = int(job.get("expiry_block", 0))
    delivery_hash = job.get("delivery_hash", "")
    asset_id = int(job.get("asset_id", 0))
    mode = int(job.get("mode", MODE_A))
    asset_id = int(job.get("asset_id", 0))

    if job_state.get("last_status") != status:
        logger.info("job %s: status -> %s (%s)", job_id, status, status_name(status))
        job_state["last_status"] = status

    if status == STATUS_OPEN and not job_state.get("refund_fired"):
        if not job_cfg.get("auto_refund_after_expiry", False):
            return
        logger.info("job %s: auto_refund_after_expiry set but daemon has no "
                    "block-height source; manual refund needed for now.", job_id)
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
        total = payment + dispute_fee
        logger.info("job %s: dispute resolved to client, firing claim, total=%s "
                    "(payment %s + dispute_fee %s)",
                    job_id, total, payment, dispute_fee)
        if shader_claim(cfg, password, job_id, total, asset_id, logger):
            job_state["claim_fired"] = True
            logger.info("job %s: claim fired ok", job_id)
        else:
            logger.error("job %s: claim failed", job_id)
        return

    if status == STATUS_REFUNDED and not job_state.get("claim_fired"):
        total = payment
        logger.info("job %s: Refunded, firing claim, total=%s", job_id, total)
        if shader_claim(cfg, password, job_id, total, asset_id, logger):
            job_state["claim_fired"] = True
            logger.info("job %s: claim fired ok", job_id)
        else:
            logger.error("job %s: claim failed", job_id)
        return

    if status == STATUS_DISPUTED:
        logger.info("job %s: Disputed, waiting for arbitrator. Daemon takes no action.", job_id)
        return

    if status in (STATUS_CLOSED, STATUS_RESOLVED_TO_BOB, STATUS_SETTLED):
        return


def handle_arbitrator_job(cfg, password, job_cfg, state, logger):
    """
    Arbitrator role: only acts on status=Disputed.
    Auto-resolves Mode B disputes by comparing chain delivery_hash to
    config expected_result_hash:
      match    -> resolve_bob (worker delivered what was agreed)
      mismatch -> resolve_alice (worker did not deliver)
    Requires expected_result_hash in config to auto-decide. Without it,
    or for Mode A jobs (which should not be Disputed in normal flow),
    logs and surfaces to operator.
    Total payout includes payment + collateral + dispute_fee (the full pool
    held by the contract during dispute).
    """
    job_id = job_cfg["job_id"]
    job_state_key = str(job_id)
    job_state = state.setdefault(job_state_key, {
        "last_status": None,
        "resolved": False,
    })

    job = shader_view_job(cfg, password, job_id, logger)
    if not job:
        logger.warning("job %s: view_job failed or no data", job_id)
        return

    status = int(job.get("status", -1))
    payment = int(job.get("payment", 0))
    collateral = int(job.get("collateral", 0))
    dispute_fee = int(job.get("dispute_fee", 0))
    delivery_hash = job.get("delivery_hash", "")
    mode = int(job.get("mode", MODE_A))
    asset_id = int(job.get("asset_id", 0))

    if job_state.get("last_status") != status:
        logger.info("job %s: status -> %s (%s)", job_id, status, status_name(status))
        job_state["last_status"] = status

    if status != STATUS_DISPUTED:
        return

    if job_state.get("resolved"):
        return

    if mode != MODE_B:
        logger.warning("job %s: Disputed but mode=%s (not Mode B). NOT auto-resolving. "
                       "Operator should review.", job_id, mode)
        return

    expected = job_cfg.get("expected_result_hash")
    if not expected:
        logger.info("job %s: Disputed, no expected_result_hash in config. "
                    "Waiting for operator decision.", job_id)
        return

    total = payment + collateral + dispute_fee
    if str(expected).lower() == str(delivery_hash).lower():
        logger.info("job %s: delivery_hash matches expected, resolving to BOB "
                    "(worker). total=%s", job_id, total)
        if shader_resolve_bob(cfg, password, job_id, total, asset_id, logger):
            job_state["resolved"] = True
            logger.info("job %s: resolve_bob fired ok", job_id)
        else:
            logger.error("job %s: resolve_bob failed", job_id)
    else:
        logger.warning("job %s: delivery_hash mismatch, chain=%s expected=%s. "
                       "Resolving to ALICE (requester). total=%s",
                       job_id, delivery_hash, expected, total)
        if shader_resolve_alice(cfg, password, job_id, total, asset_id, logger):
            job_state["resolved"] = True
            logger.info("job %s: resolve_alice fired ok", job_id)
        else:
            logger.error("job %s: resolve_alice failed", job_id)


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

    # Process batches once before entering the poll loop.
    run_batches(cfg, password, state, state_path, logger)

    logger.info("daemon ready, starting poll loop. Ctrl-C to stop.")
    try:
        while True:
            cycle_start = time.time()
            for job_cfg in cfg["jobs"]:
                try:
                    role = job_cfg["role"]
                    if role == "worker":
                        handle_worker_job(cfg, password, job_cfg, state, logger)
                    elif role == "client":
                        handle_client_job(cfg, password, job_cfg, state, logger)
                    elif role == "arbitrator":
                        handle_arbitrator_job(cfg, password, job_cfg, state, logger)
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

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

# Mode values are ASCII codes for 'A' and 'B', mirroring api.tsx.
MODE_A = 65
MODE_B = 66

DEFAULT_POLL_INTERVAL_SECONDS = 30
SHADER_TIMEOUT_SECONDS = 600


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


def shader_commit(cfg, password, job_id, collateral, logger):
    args = "role=user,action=commit," + build_args(cfg, [
        ("job_id", job_id),
        ("collateral", collateral),
        ("asset_id", 0),
    ])
    rc, _, _, _ = call_shader(cfg, password, args, logger)
    return rc == 0


def shader_submit_delivery(cfg, password, job_id, delivery_hash, mode, payment, collateral, logger):
    args = "role=user,action=submit_delivery," + build_args(cfg, [
        ("job_id", job_id),
        ("delivery_hash", delivery_hash),
        ("mode", mode),
        ("payment", payment),
        ("collateral", collateral),
        ("asset_id", 0),
    ])
    rc, _, _, _ = call_shader(cfg, password, args, logger)
    return rc == 0


def shader_claim(cfg, password, job_id, total, logger):
    args = "role=user,action=claim," + build_args(cfg, [
        ("job_id", job_id),
        ("total", total),
        ("asset_id", 0),
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


def shader_resolve_alice(cfg, password, job_id, total, logger):
    args = "role=arbitrator,action=resolve_alice," + build_args(cfg, [
        ("job_id", job_id),
        ("total", total),
        ("asset_id", 0),
    ])
    rc, _, _, _ = call_shader(cfg, password, args, logger)
    return rc == 0


def shader_resolve_bob(cfg, password, job_id, total, logger):
    args = "role=arbitrator,action=resolve_bob," + build_args(cfg, [
        ("job_id", job_id),
        ("total", total),
        ("asset_id", 0),
    ])
    rc, _, _, _ = call_shader(cfg, password, args, logger)
    return rc == 0


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

    if job_state.get("last_status") != status:
        logger.info("job %s: status -> %s (%s)", job_id, status, status_name(status))
        job_state["last_status"] = status

    if status == STATUS_OPEN and not job_state.get("commit_fired"):
        # In Mode A, the worker chooses the collateral amount in the commit call.
        # The chain only knows it after the commit lands. So we use the
        # configured expected_collateral as the amount to commit.
        expected_collateral = job_cfg.get("expected_collateral")
        if expected_collateral is None or int(expected_collateral) <= 0:
            logger.error("job %s: expected_collateral missing or zero in config. NOT firing commit.",
                         job_id)
            return
        commit_amount = int(expected_collateral)
        logger.info("job %s: firing commit, collateral=%s", job_id, commit_amount)
        if shader_commit(cfg, password, job_id, commit_amount, logger):
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
        if shader_submit_delivery(cfg, password, job_id, delivery_hash, mode, payment, collateral, logger):
            job_state["submit_fired"] = True
            logger.info("job %s: submit_delivery fired ok", job_id)
        else:
            logger.error("job %s: submit_delivery failed", job_id)
        return

    if status == STATUS_SETTLED and not job_state.get("claim_fired"):
        total = payment + collateral
        logger.info("job %s: firing claim, total=%s (payment %s + collateral %s)",
                    job_id, total, payment, collateral)
        if shader_claim(cfg, password, job_id, total, logger):
            job_state["claim_fired"] = True
            logger.info("job %s: claim fired ok, funds should be in wallet within next block",
                        job_id)
        else:
            logger.error("job %s: claim failed", job_id)
        return

    if status == STATUS_RESOLVED_TO_BOB and not job_state.get("claim_fired"):
        total = payment + collateral + int(job.get("dispute_fee", 0))
        logger.info("job %s: dispute resolved to worker, firing claim, total=%s", job_id, total)
        if shader_claim(cfg, password, job_id, total, logger):
            job_state["claim_fired"] = True
            logger.info("job %s: claim fired ok", job_id)
        else:
            logger.error("job %s: claim failed", job_id)
        return

    if status == STATUS_DISPUTED:
        logger.info("job %s: Disputed, waiting for arbitrator. Daemon takes no action.", job_id)
        return

    if status in (STATUS_CLOSED, STATUS_REFUNDED, STATUS_RESOLVED_TO_ALICE):
        # Terminal from the worker's perspective. Nothing to do.
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

    if job_state.get("last_status") != status:
        logger.info("job %s: status -> %s (%s)", job_id, status, status_name(status))
        job_state["last_status"] = status

    # Open + past expiry_block: client can refund.
    if status == STATUS_OPEN and not job_state.get("refund_fired"):
        current_height = int(job.get("_current_height", 0))  # not in view_job output today
        # Without a reliable current_height from view_job, we let operators opt in
        # via auto_refund_after_expiry: true. Daemon refunds only if config flag
        # is set; otherwise log and wait.
        if not job_cfg.get("auto_refund_after_expiry", False):
            return
        # If we can't tell what block we're at, we can't safely auto-refund.
        # For now, this branch is a future enhancement that needs a current_height
        # source. Log once per status transition and skip.
        logger.info("job %s: auto_refund_after_expiry set but daemon has no "
                    "block-height source; manual refund needed for now.", job_id)
        return

    # AwaitingApproval (Mode B only): client decides approve, dispute, or wait.
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

    # ResolvedToAlice: dispute resolved in client's favour. Claim back
    # payment + dispute_fee. Worker forfeits collateral.
    if status == STATUS_RESOLVED_TO_ALICE and not job_state.get("claim_fired"):
        total = payment + dispute_fee
        logger.info("job %s: dispute resolved to client, firing claim, total=%s "
                    "(payment %s + dispute_fee %s)",
                    job_id, total, payment, dispute_fee)
        if shader_claim(cfg, password, job_id, total, logger):
            job_state["claim_fired"] = True
            logger.info("job %s: claim fired ok", job_id)
        else:
            logger.error("job %s: claim failed", job_id)
        return

    # Refunded: refund accepted on chain, claim moves funds back to wallet.
    if status == STATUS_REFUNDED and not job_state.get("claim_fired"):
        total = payment
        logger.info("job %s: Refunded, firing claim, total=%s", job_id, total)
        if shader_claim(cfg, password, job_id, total, logger):
            job_state["claim_fired"] = True
            logger.info("job %s: claim fired ok", job_id)
        else:
            logger.error("job %s: claim failed", job_id)
        return

    if status == STATUS_DISPUTED:
        logger.info("job %s: Disputed, waiting for arbitrator. Daemon takes no action.", job_id)
        return

    if status in (STATUS_CLOSED, STATUS_RESOLVED_TO_BOB, STATUS_SETTLED):
        # Terminal from the client's perspective. Nothing to do.
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

    if job_state.get("last_status") != status:
        logger.info("job %s: status -> %s (%s)", job_id, status, status_name(status))
        job_state["last_status"] = status

    if status != STATUS_DISPUTED:
        # Arbitrator only acts on disputes. Everything else is logged and ignored.
        return

    if job_state.get("resolved"):
        # Already fired a resolution; waiting for chain to confirm.
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
        if shader_resolve_bob(cfg, password, job_id, total, logger):
            job_state["resolved"] = True
            logger.info("job %s: resolve_bob fired ok", job_id)
        else:
            logger.error("job %s: resolve_bob failed", job_id)
    else:
        logger.warning("job %s: delivery_hash mismatch, chain=%s expected=%s. "
                       "Resolving to ALICE (requester). total=%s",
                       job_id, delivery_hash, expected, total)
        if shader_resolve_alice(cfg, password, job_id, total, logger):
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

    # Validate binary and wasm exist before prompting for password.
    for p in (cfg["beam_wallet_binary"], cfg["shader_app_file"], cfg["wallet_path"]):
        if not os.path.exists(p):
            logger.error("path does not exist: %s", p)
            sys.exit(2)

    # Prompt once. Password is held in memory only.
    try:
        password = getpass.getpass("Wallet password: ")
    except (KeyboardInterrupt, EOFError):
        logger.info("daemon cancelled at password prompt")
        sys.exit(0)

    state = load_durable_state(state_path)

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

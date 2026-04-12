"""
hypertensor_trigger.py — Idios Settlement Trigger
Polls Hypertensor consensus via substrate RPC.
On ≥66% attestation → settle on Beam.
On dissent / expiry → slash or refund on Beam.

Usage:
    python hypertensor_trigger.py --job_id 2 --subnet_id 1

Config:
    Edit CONFIG block below or pass env vars.
"""

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# CONFIG — edit these or override with env vars
# ---------------------------------------------------------------------------

HYPERTENSOR_RPC_URL  = os.getenv("HYPERTENSOR_RPC_URL",  "http://127.0.0.1:9933")
BEAM_WALLET_API_URL  = os.getenv("BEAM_WALLET_API_URL",  "http://127.0.0.1:10000/api/wallet")
IDIOS_WASM_PATH      = os.getenv("IDIOS_WASM_PATH",      "/home/tones/idios/idios_app.wasm")
IDIOS_CID            = os.getenv("IDIOS_CID",
    "e595078e08f00f471e7781b8e64f1d1303fa61b838f881dd646ec5f701d9251d")

POLL_INTERVAL_SEC    = int(os.getenv("POLL_INTERVAL_SEC", "12"))   # ~1 Beam block
ATTESTATION_THRESHOLD = float(os.getenv("ATTESTATION_THRESHOLD", "0.66"))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("idios.trigger")

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class JobParams:
    job_id:      int
    subnet_id:   int
    result_hash: str
    payment:     int    # in Groth (1 BEAM = 100_000_000 Groth)
    collateral:  int
    asset_id:    int = 0

@dataclass
class ConsensusResult:
    """Parsed output from Hypertensor epoch consensus query."""
    finalized:       bool
    attestation_pct: int    # 0-100
    result_hash:     str    # hex, from consensus attest_data
    dissenting_nodes: list  # list of node pubkeys that voted against


# ---------------------------------------------------------------------------
# Hypertensor RPC helpers
# ---------------------------------------------------------------------------

def _rpc(method: str, params: list, url: str = HYPERTENSOR_RPC_URL) -> dict:
    """Raw substrate JSON-RPC call."""
    payload = {
        "jsonrpc": "2.0",
        "id":      1,
        "method":  method,
        "params":  params,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"RPC error {data['error']}")
        return data.get("result", {})
    except requests.RequestException as e:
        raise ConnectionError(f"Hypertensor RPC unreachable: {e}") from e


def get_epoch_consensus(subnet_id: int, job_id: int) -> Optional[ConsensusResult]:
    """
    Query Hypertensor for consensus receipt on a job.

    Substrate storage key pattern (adjust to actual pallet/storage name):
        SubnetConsensus.EpochResults(subnet_id, job_id)

    Returns None if epoch not yet finalized.

    ⚠️  ADAPT THIS FUNCTION to match Hypertensor's actual RPC surface.
        The structure below mirrors common Substrate consensus patterns —
        replace storage keys and field names once you have the pallet ABI.
    """
    # --- Step 1: get current epoch for subnet ---
    try:
        epoch_raw = _rpc(
            "state_getStorage",
            [_storage_key("SubnetInfo", "SubnetEpoch", subnet_id)]
        )
        if epoch_raw is None:
            log.debug("No epoch data yet for subnet %d", subnet_id)
            return None
        current_epoch = _decode_u64(epoch_raw)
    except Exception as e:
        log.warning("Could not fetch epoch for subnet %d: %s", subnet_id, e)
        return None

    # --- Step 2: fetch consensus results for this epoch ---
    try:
        result_raw = _rpc(
            "state_getStorage",
            [_storage_key("SubnetConsensus", "EpochResults", subnet_id, current_epoch)]
        )
        if result_raw is None:
            log.debug("Epoch %d not finalized yet for subnet %d", current_epoch, subnet_id)
            return None
    except Exception as e:
        log.warning("Could not fetch consensus results: %s", e)
        return None

    # --- Step 3: decode the result ---
    # This decoding depends on Hypertensor's actual SCALE codec struct.
    # Replace with proper SCALE decoding (e.g. via substrateinterface or scalecodec)
    # once you have the type registry. Placeholder structure:
    consensus_data = _decode_consensus_result(result_raw, job_id)
    return consensus_data


def _storage_key(*args) -> str:
    """
    Build a substrate storage key.
    Replace with proper xxHash/Blake2 encoding via substrateinterface:
        from substrateinterface import SubstrateInterface
        substrate = SubstrateInterface(url=HYPERTENSOR_RPC_URL)
        return substrate.create_storage_key("PalletName", "StorageName", [param1, param2])
    Stub returns empty string until wired.
    """
    log.debug("_storage_key called with args: %s (STUB — replace with real encoding)", args)
    return ""


def _decode_u64(raw_hex: str) -> int:
    """Decode little-endian u64 from hex. Replace with scalecodec if needed."""
    b = bytes.fromhex(raw_hex.lstrip("0x"))
    return int.from_bytes(b[:8], "little")


def _decode_consensus_result(raw_hex: str, job_id: int) -> Optional[ConsensusResult]:
    """
    Decode Hypertensor's consensus result struct for a given job.

    STUB — replace with actual SCALE decoding once pallet ABI is confirmed.
    Expected fields from Hypertensor docs / TG discussion:
        - is_success: bool
        - attestation_pct: u8  (0-100)
        - attest_data: Vec<u8>  (the result hash nodes voted on)
        - dissenting_validators: Vec<AccountId>

    Example using substrate-interface (install: pip install substrate-interface):
        from substrateinterface.utils.ss58 import ss58_encode
        from scalecodec.type_registry import load_type_registry_file
        # ... decode with type registry
    """
    log.warning(
        "_decode_consensus_result is a STUB — "
        "wire to Hypertensor pallet ABI before production use"
    )
    # Return None so the loop keeps polling rather than acting on bad data
    return None


# ---------------------------------------------------------------------------
# Beam Wallet API helpers
# ---------------------------------------------------------------------------

def _beam_invoke(args_str: str) -> dict:
    """
    Step 1 of two-step Beam contract call.
    Runs App Shader locally, returns raw_data for submission.
    """
    payload = {
        "jsonrpc": "2.0",
        "id":      1,
        "method":  "invoke_contract",
        "params":  {
            "contract_file": IDIOS_WASM_PATH,
            "args":          args_str,
        },
    }
    try:
        r = requests.post(BEAM_WALLET_API_URL, json=payload, timeout=15)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        raise ConnectionError(f"Beam wallet-api unreachable: {e}") from e

    if "error" in data:
        code = data["error"].get("code")
        msg  = data["error"].get("message", "")
        if code == -32019:
            raise RuntimeError(f"Contract error (Halt triggered): {msg}")
        if code == -32018:
            raise RuntimeError(f"Contract compile error (stale wasm?): {msg}")
        if code == -32020:
            raise RuntimeError(f"ACL denied: {msg}")
        raise RuntimeError(f"Beam RPC error {code}: {msg}")

    result = data.get("result", {})
    if not result.get("raw_data"):
        raise RuntimeError(f"invoke_contract returned no raw_data: {data}")
    return result


def _beam_submit(raw_data: str) -> dict:
    """Step 2 — submit raw_data to the Beam network."""
    payload = {
        "jsonrpc": "2.0",
        "id":      2,
        "method":  "process_invoke_data",
        "params":  {"data": raw_data},
    }
    try:
        r = requests.post(BEAM_WALLET_API_URL, json=payload, timeout=15)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        raise ConnectionError(f"Beam wallet-api unreachable on submit: {e}") from e

    if "error" in data:
        raise RuntimeError(f"process_invoke_data error: {data['error']}")
    return data.get("result", {})


def beam_settle(job: JobParams, attestation_pct: int) -> str:
    """Call Idios settle action on Beam. Returns txid."""
    args = (
        f"role=middleware,action=settle,"
        f"cid={IDIOS_CID},"
        f"job_id={job.job_id},"
        f"result_hash={job.result_hash},"
        f"attestation_pct={attestation_pct},"
        f"payment={job.payment},"
        f"collateral={job.collateral},"
        f"asset_id={job.asset_id}"
    )
    log.info("Calling Beam settle — job_id=%d attestation=%d%%", job.job_id, attestation_pct)
    invoke_result = _beam_invoke(args)
    submit_result = _beam_submit(invoke_result["raw_data"])
    txid = invoke_result.get("txid") or submit_result.get("txid", "unknown")
    log.info("Settle submitted — txid=%s", txid)
    return txid


def beam_slash(job: JobParams) -> str:
    """Call Idios slash action on Beam. Returns txid."""
    args = (
        f"role=middleware,action=slash,"
        f"cid={IDIOS_CID},"
        f"job_id={job.job_id},"
        f"payment={job.payment},"
        f"collateral={job.collateral},"
        f"asset_id={job.asset_id}"
    )
    log.info("Calling Beam slash — job_id=%d", job.job_id)
    invoke_result = _beam_invoke(args)
    submit_result = _beam_submit(invoke_result["raw_data"])
    txid = invoke_result.get("txid") or submit_result.get("txid", "unknown")
    log.info("Slash submitted — txid=%s", txid)
    return txid


def beam_refund(job: JobParams) -> str:
    """Call Idios refund action on Beam (job expired). Returns txid."""
    args = (
        f"role=user,action=refund,"
        f"cid={IDIOS_CID},"
        f"job_id={job.job_id}"
    )
    log.info("Calling Beam refund — job_id=%d", job.job_id)
    invoke_result = _beam_invoke(args)
    submit_result = _beam_submit(invoke_result["raw_data"])
    txid = invoke_result.get("txid") or submit_result.get("txid", "unknown")
    log.info("Refund submitted — txid=%s", txid)
    return txid


def beam_view_job(job_id: int) -> dict:
    """Read current job state from Beam contract (read-only, no tx)."""
    args = (
        f"role=user,action=view_job,"
        f"cid={IDIOS_CID},"
        f"job_id={job_id}"
    )
    payload = {
        "jsonrpc": "2.0",
        "id":      1,
        "method":  "invoke_contract",
        "params":  {
            "contract_file": IDIOS_WASM_PATH,
            "args":          args,
        },
    }
    r = requests.post(BEAM_WALLET_API_URL, json=payload, timeout=10)
    r.raise_for_status()
    return r.json().get("result", {})


# ---------------------------------------------------------------------------
# Job status constants (mirrors contract.h)
# ---------------------------------------------------------------------------

STATUS_OPEN     = 0
STATUS_ACTIVE   = 1
STATUS_SETTLED  = 2
STATUS_SLASHED  = 3
STATUS_REFUNDED = 4

TERMINAL_STATUSES = {STATUS_SETTLED, STATUS_SLASHED, STATUS_REFUNDED}


# ---------------------------------------------------------------------------
# Main polling loop
# ---------------------------------------------------------------------------

def run_trigger(job: JobParams):
    """
    Poll Hypertensor until consensus is reached, then act on Beam.
    Exits once the job reaches a terminal state.
    """
    log.info(
        "Starting trigger — job_id=%d subnet_id=%d poll_interval=%ds",
        job.job_id, job.subnet_id, POLL_INTERVAL_SEC
    )

    while True:
        # --- Check Beam job status first ---
        try:
            beam_state = beam_view_job(job.job_id)
            status = int(beam_state.get("status", -1))
            if status in TERMINAL_STATUSES:
                status_name = {2: "SETTLED", 3: "SLASHED", 4: "REFUNDED"}.get(status, str(status))
                log.info("Job %d already in terminal state: %s — exiting.", job.job_id, status_name)
                return
        except Exception as e:
            log.warning("Could not read Beam job state: %s", e)

        # --- Poll Hypertensor consensus ---
        try:
            consensus = get_epoch_consensus(job.subnet_id, job.job_id)
        except ConnectionError as e:
            log.error("Hypertensor unreachable: %s — will retry", e)
            time.sleep(POLL_INTERVAL_SEC)
            continue

        if consensus is None:
            log.debug("Consensus not finalized yet — waiting %ds", POLL_INTERVAL_SEC)
            time.sleep(POLL_INTERVAL_SEC)
            continue

        log.info(
            "Consensus received — finalized=%s attestation=%d%% result_hash=%s",
            consensus.finalized,
            consensus.attestation_pct,
            consensus.result_hash[:16] + "…",
        )

        if not consensus.finalized:
            log.debug("Epoch not yet finalized — waiting")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        # --- Validate result hash matches what was committed ---
        if consensus.result_hash and consensus.result_hash != job.result_hash:
            log.warning(
                "Result hash mismatch! committed=%s consensus=%s — slashing",
                job.result_hash[:16], consensus.result_hash[:16]
            )
            try:
                txid = beam_slash(job)
                log.info("Slashed (hash mismatch) — txid=%s", txid)
            except Exception as e:
                log.error("Slash failed: %s", e)
            return

        # --- Decide: settle or slash based on attestation ---
        if consensus.attestation_pct >= int(ATTESTATION_THRESHOLD * 100):
            try:
                txid = beam_settle(job, consensus.attestation_pct)
                log.info("✅ Settled — job_id=%d txid=%s", job.job_id, txid)
            except Exception as e:
                log.error("Settle failed: %s — will retry next poll", e)
                time.sleep(POLL_INTERVAL_SEC)
                continue
        else:
            log.warning(
                "Attestation %d%% below threshold %d%% — slashing",
                consensus.attestation_pct, int(ATTESTATION_THRESHOLD * 100)
            )
            try:
                txid = beam_slash(job)
                log.info("⚡ Slashed — job_id=%d txid=%s", job.job_id, txid)
            except Exception as e:
                log.error("Slash failed: %s — will retry", e)
                time.sleep(POLL_INTERVAL_SEC)
                continue

        return  # Terminal — done


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Idios — Hypertensor→Beam settlement trigger")
    p.add_argument("--job_id",      type=int, required=True,  help="Idios job ID")
    p.add_argument("--subnet_id",   type=int, required=True,  help="Hypertensor subnet ID")
    p.add_argument("--result_hash", type=str, required=True,  help="Expected result hash (hex 64 chars)")
    p.add_argument("--payment",     type=int, required=True,  help="Payment in Groth (0.1 BEAM = 10000000)")
    p.add_argument("--collateral",  type=int, required=True,  help="Collateral in Groth")
    p.add_argument("--asset_id",    type=int, default=0,      help="Asset ID (0=BEAM, 47=Nephrite)")
    p.add_argument("--once",        action="store_true",       help="Single check, no loop (for testing)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()

    job = JobParams(
        job_id      = args.job_id,
        subnet_id   = args.subnet_id,
        result_hash = args.result_hash,
        payment     = args.payment,
        collateral  = args.collateral,
        asset_id    = args.asset_id,
    )

    if args.once:
        # Single check — useful for testing Beam side independently
        log.info("--once mode: testing Beam connection only")
        try:
            state = beam_view_job(job.job_id)
            log.info("Beam job state: %s", state)
        except Exception as e:
            log.error("Beam test failed: %s", e)
            sys.exit(1)
        sys.exit(0)

    run_trigger(job)

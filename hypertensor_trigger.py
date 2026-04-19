"""
hypertensor_trigger.py , Idios Settlement Trigger

Watches Hypertensor for epoch consensus, then fires settle or slash on Beam.
Uses the Hypertensor class from subnet-template/subnet/substrate/chain_functions.py
directly , no guesswork, exact same patterns as consensus.py.

Usage:
    python hypertensor_trigger.py \
        --job_id 2 --subnet_id 1 \
        --result_hash aabbccddaabbccddaabbccddaabbccddaabbccddaabbccddaabbccddaabbccdd \
        --payment 10000000 --collateral 5000000 \
        --mnemonic "your twelve word mnemonic phrase here"

    # Test Beam side only
    python hypertensor_trigger.py ... --beam_test

    # Test Hypertensor connection only
    python hypertensor_trigger.py ... --ht_test

Config:
    Edit the CONFIG block or use env vars / CLI flags.

How it works:
    Uses get_reward_result_event(subnet_id, epoch) , the Network.RewardResult event ,
    which fires at the end of each epoch and carries the attestation_percentage directly.
    This is the cleanest hook: one call, definitive result, no polling storage maps.

    Flow:
        1. Wait for current epoch to close (watch epoch index advance)
        2. Query RewardResult event for that epoch
        3. attestation_percentage >= 66 → Beam settle
           attestation_percentage <  66 → Beam slash
        4. Exit once Beam job reaches terminal state

Dependencies:
    pip install requests substrate-interface tenacity websocket-client
    (same deps already in subnet-template/requirements.txt)
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
# CONFIG , edit or override via env vars / CLI
# ---------------------------------------------------------------------------

HYPERTENSOR_RPC_URL   = os.getenv("HYPERTENSOR_RPC_URL",  "ws://127.0.0.1:9944")
BEAM_WALLET_API_URL   = os.getenv("BEAM_WALLET_API_URL",  "http://127.0.0.1:10000/api/wallet")
IDIOS_WASM_PATH       = os.getenv("IDIOS_WASM_PATH",      "/home/tones/idios/idios_app.wasm")
IDIOS_CID             = os.getenv("IDIOS_CID",
    "e595078e08f00f471e7781b8e64f1d1303fa61b838f881dd646ec5f701d9251d")

# Middleware mnemonic , the wallet that holds the middleware key in the Idios contract
MIDDLEWARE_MNEMONIC   = os.getenv("MIDDLEWARE_MNEMONIC", "")

ATTESTATION_THRESHOLD = int(os.getenv("ATTESTATION_THRESHOLD", "66"))

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
    result_hash: str    # 64-char hex, committed at job creation
    payment:     int    # Groth (0.1 BEAM = 10_000_000)
    collateral:  int
    asset_id:    int = 0

@dataclass
class EpochResult:
    epoch:           int
    passed:          bool
    attestation_pct: int   # 0-100 integer (not scaled)


# ---------------------------------------------------------------------------
# Hypertensor , import the real SDK from subnet-template
# ---------------------------------------------------------------------------

def _load_hypertensor(mnemonic: str) -> "Hypertensor":
    """
    Import and initialise the Hypertensor class from subnet-template.
    Adjust sys.path if running this file outside the subnet-template directory.
    """
    subnet_template_path = os.path.expanduser("~/subnet-template")
    if subnet_template_path not in sys.path:
        sys.path.insert(0, subnet_template_path)

    try:
        from subnet.substrate.chain_functions import Hypertensor
        ht = Hypertensor(url=HYPERTENSOR_RPC_URL, phrase=mnemonic)
        log.info("Hypertensor connected to %s", HYPERTENSOR_RPC_URL)
        return ht
    except ImportError as e:
        log.error(
            "Could not import Hypertensor from subnet-template.\n"
            "Make sure ~/subnet-template is present and deps are installed:\n"
            "  cd ~/subnet-template && pip install -e .\n"
            "Error: %s", e
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Consensus detection , uses the exact same pattern as consensus.py
# ---------------------------------------------------------------------------

def get_current_epoch(ht) -> Optional[int]:
    """
    Current global epoch = block // EpochLength.
    This runtime uses global epochs, not slot-relative epochs.
    SubnetSlot does not exist in hypertensor-v2.
    """
    try:
        epoch_data = ht.get_epoch_data()
        return epoch_data.epoch
    except Exception as e:
        log.warning("Could not get epoch data: %s", e)
        return None


def get_epoch_length(ht) -> int:
    """Get EpochLength constant from chain."""
    try:
        result = ht.get_epoch_length()
        return int(str(result))
    except Exception as e:
        log.warning("Could not get epoch length: %s", e)
        return 10  # dev chain default


def get_current_subnet_epoch(ht, subnet_id: int) -> Optional[int]:
    """Current epoch , global epoch in this runtime."""
    return get_current_epoch(ht)


def wait_for_epoch_close(ht, subnet_id: int, target_epoch: int) -> bool:
    """
    Block until target_epoch has closed (current epoch > target_epoch).
    """
    epoch_length = get_epoch_length(ht)
    block_secs = 6  # dev chain ~6s blocks

    log.info("Waiting for epoch %d to close (epoch_length=%d blocks)...", target_epoch, epoch_length)
    while True:
        current = get_current_epoch(ht)
        if current is None:
            time.sleep(block_secs)
            continue

        if current > target_epoch:
            log.info("Epoch %d closed. Current epoch: %d.", target_epoch, current)
            return True

        block = ht.get_block_number()
        blocks_remaining = ((target_epoch + 1) * epoch_length) - int(str(block))
        sleep_secs = max(1, blocks_remaining * block_secs - 2)
        log.debug("Epoch %d/%d , %d blocks remaining, sleeping %ds", current, target_epoch, blocks_remaining, sleep_secs)
        time.sleep(sleep_secs)


def get_epoch_result(ht, subnet_id: int, epoch: int) -> Optional[EpochResult]:
    """
    Query the Network.RewardResult event for a closed epoch.

    RewardResult fires at the first block of the NEXT epoch:
      block = epoch_length * (epoch + 1)
    e.g. epoch 149 result is at block 1500, epoch 150 at block 1510.

    attestation_percentage is scaled by 1e18 , divide by 1e18 * 100 for 0-100.
    """
    try:
        from substrateinterface import SubstrateInterface
        si = SubstrateInterface(url=HYPERTENSOR_RPC_URL)

        epoch_length = int(str(ht.get_epoch_length()))
        # RewardResult fires at start of next epoch
        block_number = epoch_length * (epoch + 1)

        log.debug("Querying RewardResult at block %d (epoch %d)", block_number, epoch)

        block_hash = si.get_block_hash(block_number)
        if block_hash is None:
            log.debug("Block %d not found", block_number)
            return None

        events = si.get_events(block_hash)
        for event in events:
            ev = event.value.get("event", {})
            if ev.get("module_id") == "Network" and ev.get("event_id") == "RewardResult":
                attrs = ev.get("attributes", [])
                # attrs is [subnet_id, attestation_percentage]
                if isinstance(attrs, list) and len(attrs) >= 2:
                    ev_subnet_id, attestation_percentage = attrs[0], attrs[1]
                elif isinstance(attrs, dict):
                    ev_subnet_id = attrs.get("subnet_id", attrs.get(0))
                    attestation_percentage = attrs.get("attestation_percentage", attrs.get(1))
                else:
                    continue

                if int(str(ev_subnet_id)) != subnet_id:
                    continue

                pct_raw = int(str(attestation_percentage))
                # Scale: 1e18 = 100%
                attestation_pct = int(pct_raw / 1e18 * 100)
                passed = attestation_pct >= ATTESTATION_THRESHOLD

                log.info(
                    "RewardResult , subnet=%d epoch=%d attestation=%d%% → %s",
                    subnet_id, epoch, attestation_pct,
                    "PASSED ✅" if passed else "FAILED ❌"
                )
                return EpochResult(epoch=epoch, passed=passed, attestation_pct=attestation_pct)

        log.debug("No RewardResult for subnet %d at block %d", subnet_id, block_number)
        return None

    except Exception as e:
        log.warning("get_epoch_result failed for epoch %d: %s", epoch, e)
        return None


def get_epoch_result_from_consensus_data(ht, subnet_id: int, epoch: int) -> Optional[EpochResult]:
    """
    Fallback: derive attestation % from ConsensusData.attests.
    Used when RewardResult event isn't available (e.g. very fresh epoch close).

    ConsensusData.attests is a BTreeMap<subnet_node_id, AttestEntry>.
    Total eligible validators = len(get_min_class_subnet_nodes_formatted(..., Validator)).
    """
    try:
        from subnet.substrate.chain_functions import SubnetNodeClass

        consensus = ht.get_consensus_data_formatted(subnet_id, epoch)
        if consensus is None:
            log.debug("No consensus data for subnet %d epoch %d", subnet_id, epoch)
            return None

        attest_count = len(consensus.attests)

        # Get validator count for this epoch
        validators = ht.get_min_class_subnet_nodes_formatted(subnet_id, epoch, SubnetNodeClass.Validator)
        total_validators = len(validators) if validators else 0

        if total_validators == 0:
            log.warning("No validators found for subnet %d epoch %d", subnet_id, epoch)
            return None

        attestation_pct = int(attest_count / total_validators * 100)
        passed = attestation_pct >= ATTESTATION_THRESHOLD

        log.info(
            "ConsensusData fallback , subnet=%d epoch=%d attests=%d/%d (%d%%) → %s",
            subnet_id, epoch, attest_count, total_validators, attestation_pct,
            "PASSED ✅" if passed else "FAILED ❌"
        )
        return EpochResult(epoch=epoch, passed=passed, attestation_pct=attestation_pct)

    except Exception as e:
        log.warning("ConsensusData fallback failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Beam Wallet API
# ---------------------------------------------------------------------------

def _beam_invoke(args_str: str) -> dict:
    """Step 1 , run App Shader locally, returns raw_data. Does NOT broadcast."""
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method":  "invoke_contract",
        "params":  {"contract_file": IDIOS_WASM_PATH, "args": args_str},
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
        if code == -32019: raise RuntimeError(f"Contract Halt (-32019): {msg}")
        if code == -32018: raise RuntimeError(f"Compile error , restart wallet-api after wasm rebuild: {msg}")
        if code == -32020: raise RuntimeError(f"ACL denied: {msg}")
        raise RuntimeError(f"Beam error {code}: {msg}")

    result = data.get("result", {})
    if not result.get("raw_data"):
        txid = result.get("txid", "")
        if txid and txid != "00000000000000000000000000000000":
            result["_direct_txid"] = txid
            return result
        raise RuntimeError(f"invoke_contract returned no raw_data: {data}")
    return result


def _beam_submit(raw_data: str) -> dict:
    """Step 2 , broadcast to Beam network."""
    payload = {
        "jsonrpc": "2.0", "id": 2,
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


def _beam_call(args_str: str) -> str:
    """invoke + submit. Returns txid."""
    invoke = _beam_invoke(args_str)
    if "_direct_txid" in invoke:
        return invoke["_direct_txid"]
    submit = _beam_submit(invoke["raw_data"])
    return invoke.get("txid") or submit.get("txid", "unknown")


def beam_settle(job: JobParams, attestation_pct: int) -> str:
    log.info("→ Beam settle  job=%d  attestation=%d%%", job.job_id, attestation_pct)
    return _beam_call(
        f"role=middleware,action=settle,"
        f"cid={IDIOS_CID},"
        f"job_id={job.job_id},"
        f"result_hash={job.result_hash},"
        f"attestation_pct={attestation_pct},"
        f"payment={job.payment},"
        f"collateral={job.collateral},"
        f"asset_id={job.asset_id}"
    )


def beam_slash(job: JobParams) -> str:
    log.info("→ Beam slash  job=%d", job.job_id)
    return _beam_call(
        f"role=middleware,action=slash,"
        f"cid={IDIOS_CID},"
        f"job_id={job.job_id},"
        f"payment={job.payment},"
        f"collateral={job.collateral},"
        f"asset_id={job.asset_id}"
    )


def beam_view_job(job_id: int) -> dict:
    args = f"role=user,action=view_job,cid={IDIOS_CID},job_id={job_id}"
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "invoke_contract",
        "params": {"contract_file": IDIOS_WASM_PATH, "args": args},
    }
    r = requests.post(BEAM_WALLET_API_URL, json=payload, timeout=10)
    r.raise_for_status()
    return r.json().get("result", {})


STATUS_NAMES = {0:"Open", 1:"Active", 2:"Settled", 3:"Slashed", 4:"Refunded"}
TERMINAL     = {2, 3, 4}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_trigger(job: JobParams, mnemonic: str, target_epoch: Optional[int] = None, mock_settle: bool = False):
    log.info(
        "Idios trigger starting , job=%d  subnet=%d  threshold=%d%%",
        job.job_id, job.subnet_id, ATTESTATION_THRESHOLD
    )

    # ── Guard: check Beam job isn't already terminal ──────────────────────
    try:
        state = beam_view_job(job.job_id)
        status = int(state.get("status", -1))
        if status in TERMINAL:
            log.info("Job %d is already %s , nothing to do.", job.job_id, STATUS_NAMES.get(status))
            return
        log.info("Beam job %d status: %s", job.job_id, STATUS_NAMES.get(status, status))
    except Exception as e:
        log.warning("Could not read Beam job state (wallet-api running?): %s", e)

    # ── Mock settle (bypass Hypertensor) ─────────────────────────────────
    if mock_settle:
        log.info("Mock settle enabled , bypassing Hypertensor, firing settle directly")
        try:
            txid = beam_settle(job, 100)
            log.info("✅ Settled (mock) , job=%d  txid=%s", job.job_id, txid)
        except Exception as e:
            log.error("Settle failed: %s", e)
            sys.exit(1)
        return

    # ── Connect to Hypertensor ────────────────────────────────────────────
    ht = _load_hypertensor(mnemonic)

    # ── Determine which epoch to watch ───────────────────────────────────
    if target_epoch is not None:
        epoch = target_epoch
        log.info("Watching specific epoch %d", epoch)
    else:
        current = get_current_subnet_epoch(ht, job.subnet_id)
        if current is None:
            log.error("Cannot determine current epoch for subnet %d , is the node running?", job.subnet_id)
            sys.exit(1)
        epoch = current
        log.info("Current epoch: %d , will watch for this epoch to close", epoch)

    # ── Wait for epoch to close ───────────────────────────────────────────
    if not wait_for_epoch_close(ht, job.subnet_id, epoch):
        log.error("Epoch wait failed , exiting")
        sys.exit(1)

    # ── Get result , try RewardResult event first, fall back to ConsensusData ──
    result = get_epoch_result(ht, job.subnet_id, epoch)

    if result is None:
        log.info("RewardResult event not found, trying ConsensusData fallback...")
        result = get_epoch_result_from_consensus_data(ht, job.subnet_id, epoch)

    if result is None:
        log.error(
            "Could not determine consensus result for subnet %d epoch %d.\n"
            "The epoch may not have had a validator submission. Consider calling beam refund if the job has expired.",
            job.subnet_id, epoch
        )
        sys.exit(1)

    # ── Act on Beam ───────────────────────────────────────────────────────
    if result.passed:
        try:
            txid = beam_settle(job, result.attestation_pct)
            log.info("✅ Settled , job=%d  epoch=%d  txid=%s", job.job_id, epoch, txid)
        except Exception as e:
            log.error("Settle failed: %s", e)
            sys.exit(1)
    else:
        try:
            txid = beam_slash(job)
            log.info("⚡ Slashed , job=%d  epoch=%d  txid=%s", job.job_id, epoch, txid)
        except Exception as e:
            log.error("Slash failed: %s", e)
            sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="Idios , Hypertensor→Beam settlement trigger",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Normal , watch current epoch, settle or slash when it closes
  python hypertensor_trigger.py \\
    --job_id 2 --subnet_id 1 \\
    --result_hash aabbccddaabbccddaabbccddaabbccddaabbccddaabbccddaabbccddaabbccdd \\
    --payment 10000000 --collateral 5000000 \\
    --mnemonic "your twelve word mnemonic phrase here"

  # Watch a specific epoch (already closed or about to close)
  python hypertensor_trigger.py ... --epoch 42

  # Test Beam connection only (no Hypertensor)
  python hypertensor_trigger.py ... --beam_test

  # Test Hypertensor connection only
  python hypertensor_trigger.py ... --ht_test

Env vars (alternative to CLI flags):
  HYPERTENSOR_RPC_URL   default: ws://127.0.0.1:9944
  BEAM_WALLET_API_URL   default: http://127.0.0.1:10000/api/wallet
  MIDDLEWARE_MNEMONIC   mnemonic for the middleware wallet
  IDIOS_CID             contract ID
  ATTESTATION_THRESHOLD default: 66
        """
    )
    p.add_argument("--job_id",      type=int, required=True)
    p.add_argument("--subnet_id",   type=int, required=True)
    p.add_argument("--result_hash", type=str, required=True,
                   help="64-char hex hash committed at job creation")
    p.add_argument("--payment",     type=int, required=True,
                   help="Groth (0.1 BEAM = 10_000_000)")
    p.add_argument("--collateral",  type=int, required=True)
    p.add_argument("--asset_id",    type=int, default=0, help="0=BEAM, 47=Nephrite")
    p.add_argument("--epoch",       type=int, default=None,
                   help="Specific epoch to watch (default: current epoch)")
    p.add_argument("--mnemonic",    type=str,
                   default=MIDDLEWARE_MNEMONIC,
                   help="Middleware wallet mnemonic (or set MIDDLEWARE_MNEMONIC env var)")
    p.add_argument("--beam_test",   action="store_true",
                   help="Test Beam connection only , reads job state and exits")
    p.add_argument("--ht_test",     action="store_true",
                   help="Test Hypertensor connection only , reads epoch and exits")
    p.add_argument("--mock_settle",  action="store_true",
                   help="Mock a passing attestation (100%%) , bypasses Hypertensor, fires settle directly")
    args = p.parse_args()

    job = JobParams(
        job_id=args.job_id, subnet_id=args.subnet_id,
        result_hash=args.result_hash, payment=args.payment,
        collateral=args.collateral, asset_id=args.asset_id,
    )

    if args.beam_test:
        log.info("=== Beam connection test ===")
        try:
            state = beam_view_job(job.job_id)
            status = int(state.get("status", -1))
            log.info(
                "Job %d: status=%s  payment=%s  collateral=%s",
                job.job_id, STATUS_NAMES.get(status, status),
                state.get("payment"), state.get("collateral")
            )
        except Exception as e:
            log.error("FAILED: %s", e)
            log.error("Is wallet-api running?  cd ~/beam-cli && ./wallet-api --use_http=1 --port=10000 ...")
            sys.exit(1)
        return

    if args.ht_test:
        if not args.mnemonic:
            log.error("--mnemonic required for Hypertensor test")
            sys.exit(1)
        log.info("=== Hypertensor connection test ===")
        ht = _load_hypertensor(args.mnemonic)
        try:
            block = ht.get_block_number()
            epoch = get_current_epoch(ht)
            epoch_length = get_epoch_length(ht)
            log.info("Block: %s  Epoch: %s  EpochLength: %s", block, epoch, epoch_length)
            subnet_epoch = get_current_subnet_epoch(ht, job.subnet_id)
            log.info("Subnet %d current epoch: %s", job.subnet_id, subnet_epoch)
        except Exception as e:
            log.error("FAILED: %s", e)
            sys.exit(1)
        return

    if not args.mnemonic:
        log.error("--mnemonic required (or set MIDDLEWARE_MNEMONIC env var)")
        sys.exit(1)

    run_trigger(job, mnemonic=args.mnemonic, target_epoch=args.epoch, mock_settle=args.mock_settle)


if __name__ == "__main__":
    main()

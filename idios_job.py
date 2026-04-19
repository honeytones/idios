"""
idios_job.py , Idios Complete Job Flow

Single script for a requester to create and monitor a complete Idios job.

Flow:
    1. Encrypt job payload with node's RSA pubkey
    2. Upload encrypted payload to Beam private IPFS
    3. Compute result hash from expected output
    4. Create job on Beam contract (locks payment + commits result hash)
    5. Wait for node to commit collateral (job goes Active)
    6. Start watching Hypertensor consensus
    7. Settle or slash automatically when epoch closes

Usage:
    # Create a job
    python3 idios_job.py create \
        --job_id 3 \
        --subnet_id 1 \
        --node_beam_pk 23f0cd450ef9225decffb4312d5f74e07afad0f59333bcd29479ed20214729da00 \
        --node_rsa_pubkey ~/.idios/node_rsa_pubkey.pem \
        --payload '{"model": "llama2", "prompt": "Summarise this contract"}' \
        --expected_result '{"summary": "The contract is valid and binding."}' \
        --payment 10000000 \
        --expiry_block 3900000

    # Watch and settle an existing job
    python3 idios_job.py watch \
        --job_id 3 \
        --subnet_id 1 \
        --result_hash <hash_from_create> \
        --payment 10000000 \
        --collateral 5000000 \
        --mnemonic "your twelve word mnemonic"

    # View job status
    python3 idios_job.py status --job_id 3

    # Full flow (create + watch in one command)
    python3 idios_job.py run \
        --job_id 3 \
        --subnet_id 1 \
        --node_beam_pk 23f0cd... \
        --node_rsa_pubkey ~/.idios/node_rsa_pubkey.pem \
        --payload '{"model": "llama2", "prompt": "..."}' \
        --expected_result '{"output": "..."}' \
        --payment 10000000 \
        --collateral 5000000 \
        --expiry_block 3900000 \
        --mnemonic "your twelve word mnemonic"
"""

import argparse
import json
import logging
import os
import sys
import time
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BEAM_WALLET_API    = os.getenv("BEAM_WALLET_API",    "http://127.0.0.1:10000/api/wallet")
HYPERTENSOR_RPC    = os.getenv("HYPERTENSOR_RPC",    "ws://127.0.0.1:9944")
IDIOS_WASM_PATH    = os.getenv("IDIOS_WASM_PATH",    "idios_app.wasm")
IDIOS_CID          = os.getenv("IDIOS_CID",
    "e595078e08f00f471e7781b8e64f1d1303fa61b838f881dd646ec5f701d9251d")
BEAM_NODE_ADDR     = os.getenv("BEAM_NODE_ADDR",     "eu-node01.mainnet.beam.mw:8100")
MIDDLEWARE_MNEMONIC = os.getenv("MIDDLEWARE_MNEMONIC", "")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("idios.job")

# ---------------------------------------------------------------------------
# Beam contract helpers
# ---------------------------------------------------------------------------

def _beam_invoke(args_str: str) -> dict:
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "invoke_contract",
        "params": {"contract_file": IDIOS_WASM_PATH, "args": args_str},
    }
    r = requests.post(BEAM_WALLET_API, json=payload, timeout=15)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        code = data["error"].get("code")
        msg  = data["error"].get("message", "")
        if code == -32019: raise RuntimeError(f"Contract Halt: {msg}")
        if code == -32018: raise RuntimeError(f"Compile error (restart wallet-api?): {msg}")
        raise RuntimeError(f"Beam error {code}: {msg}")
    result = data.get("result", {})
    if not result.get("raw_data"):
        # Contract executed directly and returned a txid , no process step needed
        txid = result.get("txid", "")
        if txid and txid != "00000000000000000000000000000000":
            result["_direct_txid"] = txid
            return result
        raise RuntimeError(f"No raw_data returned: {data}")
    return result

def _beam_submit(raw_data: str) -> dict:
    payload = {
        "jsonrpc": "2.0", "id": 2,
        "method": "process_invoke_data",
        "params": {"data": raw_data},
    }
    r = requests.post(BEAM_WALLET_API, json=payload, timeout=15)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"Submit error: {data['error']}")
    return data.get("result", {})

def _beam_call(args_str: str) -> str:
    invoke = _beam_invoke(args_str)
    if "_direct_txid" in invoke:
        return invoke["_direct_txid"]
    submit = _beam_submit(invoke["raw_data"])
    return invoke.get("txid") or submit.get("txid", "unknown")

def beam_create_job(job_id: int, subnet_id: int, node_pk: str, result_hash: str,
                    payment: int, expiry_block: int, asset_id: int = 0) -> str:
    log.info("Creating job %d on Beam...", job_id)
    args = (
        f"role=user,action=create,"
        f"cid={IDIOS_CID},"
        f"job_id={job_id},"
        f"subnet_id={subnet_id},"
        f"epoch=1,"
        f"expiry_block={expiry_block},"
        f"payment={payment},"
        f"asset_id={asset_id},"
        f"node_pk={node_pk},"
        f"result_hash={result_hash}"
    )
    txid = _beam_call(args)
    log.info("Job created , txid=%s", txid)
    return txid

def beam_view_job(job_id: int) -> dict:
    """View job state using beam-wallet CLI , wallet-api invoke_contract cannot read contract vars."""
    import subprocess, json as _json
    beam_cli = os.getenv("BEAM_CLI_PATH", "beam-wallet")
    args_str = (
        f"role=user,action=view_job,"
        f"cid={IDIOS_CID},"
        f"job_id={job_id}"
    )
    cmd = [
        beam_cli, "shader",
        "--config_file=beam-wallet.cfg",
        "--wallet_path=wallet.db",
        f"--shader_app_file={IDIOS_WASM_PATH}",
        f"--shader_args={args_str}",
        f"--node_addr={BEAM_NODE_ADDR}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        for line in output.splitlines():
            if "Shader output:" in line:
                data_str = line.split("Shader output:", 1)[1].strip()
                if data_str.startswith('"'):
                    data_str = "{" + data_str + "}"
                parsed = _json.loads(data_str)
                return parsed.get("job", {})
    except Exception as e:
        log.warning("beam_view_job CLI failed: %s", e)
    return {}

STATUS_NAMES = {0: "Open", 1: "Active", 2: "Settled", 3: "Slashed", 4: "Refunded"}
TERMINAL = {2, 3, 4}

# ---------------------------------------------------------------------------
# Wait for node commit
# ---------------------------------------------------------------------------

def wait_for_active(job_id: int, timeout_secs: int = 600) -> bool:
    """Wait until the node commits collateral and job goes Active."""
    log.info("Waiting for node to commit collateral (job_id=%d)...", job_id)
    deadline = time.time() + timeout_secs
    while time.time() < deadline:
        try:
            state = beam_view_job(job_id)
            status = int(state.get("status", -1))
            log.info("Job %d status: %s", job_id, STATUS_NAMES.get(status, status))
            if status == 1:  # Active
                log.info("Job %d is Active , node committed collateral", job_id)
                return True
            if status in TERMINAL:
                log.warning("Job %d already in terminal state: %s", job_id, STATUS_NAMES.get(status))
                return False
        except Exception as e:
            log.warning("Could not read job state: %s", e)
        time.sleep(12)
    log.error("Timed out waiting for node commit after %ds", timeout_secs)
    return False

# ---------------------------------------------------------------------------
# Trigger (imported from hypertensor_trigger.py)
# ---------------------------------------------------------------------------

def run_trigger(job_id: int, subnet_id: int, result_hash: str,
                payment: int, collateral: int, mnemonic: str,
                asset_id: int = 0, target_epoch: Optional[int] = None):
    """Import and run the Hypertensor consensus trigger."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from hypertensor_trigger import JobParams, run_trigger as _run_trigger
    except ImportError:
        log.error("hypertensor_trigger.py not found in same directory")
        sys.exit(1)

    job = JobParams(
        job_id=job_id,
        subnet_id=subnet_id,
        result_hash=result_hash,
        payment=payment,
        collateral=collateral,
        asset_id=asset_id,
    )
    os.environ["HYPERTENSOR_RPC_URL"] = HYPERTENSOR_RPC
    os.environ["BEAM_WALLET_API_URL"]  = BEAM_WALLET_API
    os.environ["IDIOS_WASM_PATH"]      = IDIOS_WASM_PATH
    os.environ["IDIOS_CID"]            = IDIOS_CID

    _run_trigger(job, mnemonic=mnemonic, target_epoch=target_epoch)

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_create(args):
    """Encrypt payload, upload to IPFS, create job on Beam."""
    from idios_payload import RequesterPayload, hash_result

    # Load node RSA pubkey
    node_rsa_pubkey_path = os.path.expanduser(args.node_rsa_pubkey)
    if not os.path.exists(node_rsa_pubkey_path):
        log.error("Node RSA pubkey not found: %s", node_rsa_pubkey_path)
        sys.exit(1)
    with open(node_rsa_pubkey_path, "rb") as f:
        node_rsa_pubkey_pem = f.read()

    # Parse payload
    try:
        payload = json.loads(args.payload)
    except json.JSONDecodeError:
        payload = args.payload  # treat as plain string

    # Parse expected result
    try:
        expected_result = json.loads(args.expected_result)
    except (json.JSONDecodeError, TypeError):
        expected_result = args.expected_result

    # Prepare job (encrypt + upload)
    r = RequesterPayload(beam_api_url=BEAM_WALLET_API)
    cid, result_hash = r.prepare_job(
        payload=payload,
        node_rsa_pubkey_pem=node_rsa_pubkey_pem,
        expected_result=expected_result,
    )

    log.info("Payload CID: %s", cid)
    log.info("Result hash: %s", result_hash)

    # Create job on Beam
    txid = beam_create_job(
        job_id=args.job_id,
        subnet_id=args.subnet_id,
        node_pk=args.node_beam_pk,
        result_hash=result_hash,
        payment=args.payment,
        expiry_block=args.expiry_block,
        asset_id=args.asset_id,
    )

    print(f"\nJob created successfully:")
    print(f"  Job ID:      {args.job_id}")
    print(f"  CID:         {cid}")
    print(f"  Result hash: {result_hash}")
    print(f"  Txid:        {txid}")
    print(f"\nSend the CID to the node operator: {cid}")
    print(f"\nTo watch for settlement:")
    print(f"  python3 idios_job.py watch --job_id {args.job_id} --subnet_id {args.subnet_id} \\")
    print(f"    --result_hash {result_hash} --payment {args.payment} --collateral <NODE_COLLATERAL> \\")
    print(f"    --mnemonic \"your mnemonic\"")


def cmd_watch(args):
    """Watch for consensus and settle/slash."""
    mnemonic = args.mnemonic or MIDDLEWARE_MNEMONIC
    if not mnemonic:
        log.error("--mnemonic required (or set MIDDLEWARE_MNEMONIC env var)")
        sys.exit(1)

    run_trigger(
        job_id=args.job_id,
        subnet_id=args.subnet_id,
        result_hash=args.result_hash,
        payment=args.payment,
        collateral=args.collateral,
        mnemonic=mnemonic,
        asset_id=args.asset_id,
        target_epoch=args.epoch,
    )


def cmd_status(args):
    """Print current job status."""
    try:
        state = beam_view_job(args.job_id)
        status = int(state.get("status", -1))
        print(f"Job {args.job_id}:")
        print(f"  Status:     {STATUS_NAMES.get(status, status)}")
        print(f"  Payment:    {state.get('payment', 'N/A')}")
        print(f"  Collateral: {state.get('collateral', 'N/A')}")
    except Exception as e:
        log.error("Could not read job status: %s", e)
        sys.exit(1)


def cmd_run(args):
    """Full flow , create job, wait for commit, watch for settlement."""
    from idios_payload import RequesterPayload

    mnemonic = args.mnemonic or MIDDLEWARE_MNEMONIC
    if not mnemonic:
        log.error("--mnemonic required for watch phase")
        sys.exit(1)

    # Load node RSA pubkey
    node_rsa_pubkey_path = os.path.expanduser(args.node_rsa_pubkey)
    with open(node_rsa_pubkey_path, "rb") as f:
        node_rsa_pubkey_pem = f.read()

    # Parse payload and expected result
    try:
        payload = json.loads(args.payload)
    except json.JSONDecodeError:
        payload = args.payload
    try:
        expected_result = json.loads(args.expected_result)
    except (json.JSONDecodeError, TypeError):
        expected_result = args.expected_result

    # Prepare and upload
    r = RequesterPayload(beam_api_url=BEAM_WALLET_API)
    cid, result_hash = r.prepare_job(
        payload=payload,
        node_rsa_pubkey_pem=node_rsa_pubkey_pem,
        expected_result=expected_result,
    )
    log.info("Payload CID: %s", cid)
    log.info("Result hash: %s", result_hash)

    # Create on Beam
    beam_create_job(
        job_id=args.job_id,
        subnet_id=args.subnet_id,
        node_pk=args.node_beam_pk,
        result_hash=result_hash,
        payment=args.payment,
        expiry_block=args.expiry_block,
        asset_id=args.asset_id,
    )

    # Wait for node commit
    if not wait_for_active(args.job_id):
        log.error("Job never went Active , node did not commit. Check node is running.")
        sys.exit(1)

    # Watch for consensus and settle
    run_trigger(
        job_id=args.job_id,
        subnet_id=args.subnet_id,
        result_hash=result_hash,
        payment=args.payment,
        collateral=args.collateral,
        mnemonic=mnemonic,
        asset_id=args.asset_id,
    )

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="Idios , complete job flow",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd")

    # -- create --
    c = sub.add_parser("create", help="Encrypt payload, upload to IPFS, create job on Beam")
    c.add_argument("--job_id",        type=int, required=True)
    c.add_argument("--subnet_id",     type=int, required=True)
    c.add_argument("--node_beam_pk",  type=str, required=True, help="Node's Beam wallet pubkey (hex)")
    c.add_argument("--node_rsa_pubkey", type=str, required=True, help="Path to node's RSA public key PEM")
    c.add_argument("--payload",       type=str, required=True, help="Job input as JSON string or plain text")
    c.add_argument("--expected_result", type=str, required=True, help="Expected output as JSON string")
    c.add_argument("--payment",       type=int, required=True, help="Groth (0.1 BEAM = 10_000_000)")
    c.add_argument("--expiry_block",  type=int, required=True)
    c.add_argument("--asset_id",      type=int, default=0)

    # -- watch --
    w = sub.add_parser("watch", help="Watch for consensus and settle/slash")
    w.add_argument("--job_id",      type=int, required=True)
    w.add_argument("--subnet_id",   type=int, required=True)
    w.add_argument("--result_hash", type=str, required=True)
    w.add_argument("--payment",     type=int, required=True)
    w.add_argument("--collateral",  type=int, required=True)
    w.add_argument("--mnemonic",    type=str, default=MIDDLEWARE_MNEMONIC)
    w.add_argument("--asset_id",    type=int, default=0)
    w.add_argument("--epoch",       type=int, default=None)

    # -- status --
    s = sub.add_parser("status", help="View job status on Beam")
    s.add_argument("--job_id", type=int, required=True)

    # -- run --
    r = sub.add_parser("run", help="Full flow , create, wait for commit, watch, settle")
    r.add_argument("--job_id",        type=int, required=True)
    r.add_argument("--subnet_id",     type=int, required=True)
    r.add_argument("--node_beam_pk",  type=str, required=True)
    r.add_argument("--node_rsa_pubkey", type=str, required=True)
    r.add_argument("--payload",       type=str, required=True)
    r.add_argument("--expected_result", type=str, required=True)
    r.add_argument("--payment",       type=int, required=True)
    r.add_argument("--collateral",    type=int, required=True)
    r.add_argument("--expiry_block",  type=int, required=True)
    r.add_argument("--mnemonic",      type=str, default=MIDDLEWARE_MNEMONIC)
    r.add_argument("--asset_id",      type=int, default=0)

    args = p.parse_args()

    if args.cmd == "create":   cmd_create(args)
    elif args.cmd == "watch":  cmd_watch(args)
    elif args.cmd == "status": cmd_status(args)
    elif args.cmd == "run":    cmd_run(args)
    else:
        p.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()

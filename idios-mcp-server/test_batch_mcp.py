#!/usr/bin/env python3
"""
Live test for the batch_create_contracts MCP tool, on the TEST cid only.

Drives the real MCP server over stdio against a real wallet:
  1. get_chain_info, get_key
  2. batch_create_contracts with --count small Mode B specs (self dealing,
     worker pubkey = own key), count 1 to 50, default 2
  3. view each contract, expect Open
  4. drain, two modes:
     cancel (default): commit_collateral then mutual_cancel per job,
       final state Cancelled. Two transactions per job, slow at high counts.
     refund: refund_contract per job straight from Open, no commit,
       final state Refunded. One transaction per job, use this for big runs.
  5. view each, expect the drain mode's final state

Usage:
  read -s -p "Wallet password: " IDIOS_WALLET_PASS && export IDIOS_WALLET_PASS && echo
  python3 test_batch_mcp.py --config /path/to/test_cid_config.json --job-base 88801
  python3 test_batch_mcp.py --config ... --job-base 90001 --count 50 --drain refund --payment 1000000

A 50 count refund run is about 51 sequential chain confirmed transactions,
expect 60 to 90 minutes unattended and roughly 1.1 BEAM in fees total.

The config MUST point at the test cid, never production. State changing
calls wait for chain confirmation, so the whole run takes several minutes.
Total spend is transaction fees only; payments and collateral cycle back.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time

PAYMENT = 5_000_000        # 0.05 BEAM
DISPUTE_FEE = 1_000_000    # 0.01 BEAM
COLLATERAL = 2_000_000     # 0.02 BEAM
EXPIRY_MARGIN = 300
REVIEW_WINDOW = 20
CALL_TIMEOUT = 660


class McpClient:
    def __init__(self, server_path, config_path, password):
        env = dict(os.environ)
        env["IDIOS_WALLET_PASS"] = password
        self.proc = subprocess.Popen(
            [sys.executable, server_path, "--config", config_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env,
        )
        self.next_id = 0
        self.pending = {}
        self.lock = threading.Lock()
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()
        self._request("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "batch-test", "version": "1.0"},
        })
        self._notify("notifications/initialized")

    def _read_loop(self):
        for raw in self.proc.stdout:
            try:
                msg = json.loads(raw.decode())
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            mid = msg.get("id")
            if mid is not None:
                with self.lock:
                    ev = self.pending.get(mid)
                    if ev:
                        ev[1] = msg
                        ev[0].set()

    def _send(self, obj):
        self.proc.stdin.write((json.dumps(obj) + "\n").encode())
        self.proc.stdin.flush()

    def _notify(self, method):
        self._send({"jsonrpc": "2.0", "method": method})

    def _request(self, method, params, timeout=CALL_TIMEOUT):
        self.next_id += 1
        mid = self.next_id
        ev = [threading.Event(), None]
        with self.lock:
            self.pending[mid] = ev
        self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
        if not ev[0].wait(timeout):
            raise TimeoutError("no response to {} within {}s".format(method, timeout))
        with self.lock:
            del self.pending[mid]
        return ev[1]

    def call_tool(self, name, arguments=None):
        resp = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        if "error" in resp:
            raise RuntimeError("{} protocol error: {}".format(name, resp["error"]))
        return resp["result"]["content"][0]["text"]

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        self.proc.terminate()


def step(label, text):
    print("== {} ==".format(label))
    print(text.strip()[:400])
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--job-base", type=int, default=88801)
    ap.add_argument("--count", type=int, default=2, help="number of specs in the batch, 1 to 50")
    ap.add_argument("--drain", choices=("cancel", "refund"), default="cancel",
                    help="cancel: commit then mutual_cancel per job. refund: refund from Open, one tx per job")
    ap.add_argument("--payment", type=int, default=PAYMENT, help="payment per job in groth")
    ap.add_argument("--drain-only", action="store_true",
                    help="skip creation, drain existing jobs job-base..job-base+count-1. Refund needs the chain past each job's expiry_block")
    args = ap.parse_args()
    if not 1 <= args.count <= 50:
        print("--count must be 1 to 50 (contract nMaxCount is 50).", file=sys.stderr)
        sys.exit(1)

    password = os.environ.get("IDIOS_WALLET_PASS")
    if not password:
        print("Set IDIOS_WALLET_PASS in the environment first.", file=sys.stderr)
        sys.exit(1)

    cfg = json.load(open(args.config))
    if cfg.get("cid", "").startswith("41ef8be5"):
        print("REFUSING: config points at the PRODUCTION cid. Use the test cid config.", file=sys.stderr)
        sys.exit(1)

    server = os.path.join(os.path.dirname(os.path.abspath(__file__)), "idios_mcp_server.py")
    job_ids = list(range(args.job_base, args.job_base + args.count))

    client = McpClient(server, args.config, password)
    try:
        info = client.call_tool("get_chain_info")
        step("chain info", info)
        digits = "".join(ch for ch in info.split(".")[0] if ch.isdigit())
        if not digits:
            print("Could not parse block height, aborting before any funds move.", file=sys.stderr)
            sys.exit(1)
        expiry = int(digits) + EXPIRY_MARGIN

        key_text = client.call_tool("get_key")
        step("own key", key_text)
        pk = key_text.strip().split()[-1]
        if len(pk) < 64:
            print("Could not parse pubkey, aborting before any funds move.", file=sys.stderr)
            sys.exit(1)

        if not args.drain_only:
            for jid in job_ids:
                state = client.call_tool("view_contract", {"job_id": jid})
                if '"job_id"' in state:
                    print("Job id {} already exists on this cid. Rerun with a different --job-base.".format(jid), file=sys.stderr)
                    sys.exit(1)

        specs = []
        for jid in job_ids:
            specs.append({
                "job_id": jid, "worker_pubkey": pk,
                "payment": args.payment, "asset_id": 0,
                "expiry_block": expiry, "dispute_fee": DISPUTE_FEE,
                "review_window_blocks": REVIEW_WINDOW,
            })
        if not args.drain_only:
            result = client.call_tool("batch_create_contracts", {"specs": specs})
            step("batch create", result)
            if result.startswith("Error"):
                sys.exit(1)

            time.sleep(10)
            view_after = job_ids if args.count <= 5 else [job_ids[0], job_ids[-1]]
            for jid in view_after:
                step("view {} after create".format(jid), client.call_tool("view_contract", {"job_id": jid}))

        if args.drain == "cancel":
            for jid in job_ids:
                step("commit {}".format(jid), client.call_tool("commit_collateral", {"job_id": jid, "collateral": COLLATERAL}))
            for jid in job_ids:
                step("mutual cancel {}".format(jid), client.call_tool("mutual_cancel", {"job_id": jid}))
            want = "Cancelled"
        else:
            first = client.call_tool("view_contract", {"job_id": job_ids[0]})
            if "Refunded" not in first and "Open" not in first and "Active" not in first:
                print("Job {} is not refundable (state below). Aborting drain.".format(job_ids[0]), file=sys.stderr)
                print(first, file=sys.stderr)
                sys.exit(1)
            for i, jid in enumerate(job_ids, 1):
                state = client.call_tool("view_contract", {"job_id": jid})
                if "Refunded" in state:
                    print("== refund {} ({} of {}) == already Refunded, skipping".format(jid, i, len(job_ids)))
                    continue
                step("refund {} ({} of {})".format(jid, i, len(job_ids)), client.call_tool("refund_contract", {"job_id": jid}))
            want = "Refunded"

        ok = True
        bad = []
        for jid in job_ids:
            state = client.call_tool("view_contract", {"job_id": jid})
            if want not in state:
                ok = False
                bad.append(jid)
                step("final state {}".format(jid), state)
        print("RESULT: {}".format(
            "PASS, all {} contracts {}, funds drained back".format(len(job_ids), want) if ok
            else "CHECK NEEDED, not {}: {}".format(want, bad)))
        sys.exit(0 if ok else 2)
    finally:
        client.close()


if __name__ == "__main__":
    main()

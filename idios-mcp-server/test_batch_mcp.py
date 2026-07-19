#!/usr/bin/env python3
"""
Live test for the batch_create_contracts MCP tool, on the TEST cid only.

Drives the real MCP server over stdio against a real wallet:
  1. get_chain_info, get_key
  2. batch_create_contracts with two small Mode B specs (self dealing,
     worker pubkey = own key)
  3. view both contracts, expect Open
  4. commit_collateral on both
  5. mutual_cancel both (self deal satisfies both signatures), draining all
     funds straight back to the wallet
  6. view both, expect Cancelled

Usage:
  read -s -p "Wallet password: " IDIOS_WALLET_PASS && export IDIOS_WALLET_PASS && echo
  python3 test_batch_mcp.py --config /path/to/test_cid_config.json --job-base 88801

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
    args = ap.parse_args()

    password = os.environ.get("IDIOS_WALLET_PASS")
    if not password:
        print("Set IDIOS_WALLET_PASS in the environment first.", file=sys.stderr)
        sys.exit(1)

    cfg = json.load(open(args.config))
    if cfg.get("cid", "").startswith("41ef8be5"):
        print("REFUSING: config points at the PRODUCTION cid. Use the test cid config.", file=sys.stderr)
        sys.exit(1)

    server = os.path.join(os.path.dirname(os.path.abspath(__file__)), "idios_mcp_server.py")
    job_a = args.job_base
    job_b = args.job_base + 1

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

        for jid in (job_a, job_b):
            state = client.call_tool("view_contract", {"job_id": jid})
            if '"job_id"' in state:
                print("Job id {} already exists on this cid. Rerun with a different --job-base.".format(jid), file=sys.stderr)
                sys.exit(1)

        specs = []
        for jid in (job_a, job_b):
            specs.append({
                "job_id": jid, "worker_pubkey": pk,
                "payment": PAYMENT, "asset_id": 0,
                "expiry_block": expiry, "dispute_fee": DISPUTE_FEE,
                "review_window_blocks": REVIEW_WINDOW,
            })
        result = client.call_tool("batch_create_contracts", {"specs": specs})
        step("batch create", result)
        if result.startswith("Error"):
            sys.exit(1)

        time.sleep(10)
        for jid in (job_a, job_b):
            step("view {} after create".format(jid), client.call_tool("view_contract", {"job_id": jid}))

        for jid in (job_a, job_b):
            step("commit {}".format(jid), client.call_tool("commit_collateral", {"job_id": jid, "collateral": COLLATERAL}))

        for jid in (job_a, job_b):
            step("mutual cancel {}".format(jid), client.call_tool("mutual_cancel", {"job_id": jid}))

        ok = True
        for jid in (job_a, job_b):
            state = client.call_tool("view_contract", {"job_id": jid})
            step("final state {}".format(jid), state)
            if "Cancelled" not in state:
                ok = False
        print("RESULT: {}".format("PASS, both contracts Cancelled, funds drained back" if ok else "CHECK NEEDED, a contract is not Cancelled"))
        sys.exit(0 if ok else 2)
    finally:
        client.close()


if __name__ == "__main__":
    main()

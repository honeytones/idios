#!/usr/bin/env python3
"""idios_doctor.py: preflight check for the Idios MCP server.

Run this with the SAME python interpreter that will run the server
(usually your venv's python), pointing at your config:

    python3 idios_doctor.py --config /path/to/idios_mcp_config.json

It checks the environment the server needs and fails loudly with a
fixable message for each problem, instead of the server dying silently
and the MCP client hanging at initialize.

It never uses your wallet password, never spends funds, and never makes
a chain call. The node check is a plain TCP connect only.

Exit code 0 means every check passed (warnings allowed). Exit code 1
means at least one check failed. Standard library only.
"""

import argparse
import json
import os
import socket
import sys

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"

REQUIRED_KEYS = [
    "beam_wallet_binary",
    "shader_app_file",
    "wallet_path",
    "node_addr",
    "cid",
]

results = []


def report(status, name, detail):
    line = f"[{status}] {name}: {detail}"
    print(line)
    results.append(status)


def check_python_version():
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 10):
        report(PASS, "python version", f"{ver} (need 3.10 or newer)")
    else:
        report(
            FAIL,
            "python version",
            f"{ver} is too old, the server needs 3.10 or newer. "
            "Install a newer python or point your venv at one.",
        )


def check_mcp_import():
    try:
        import mcp  # noqa: F401
    except ImportError:
        report(
            FAIL,
            "mcp package",
            "the mcp package does not import in THIS interpreter "
            f"({sys.executable}). This is the most common silent failure. "
            "Fix: run this interpreter's pip, for example "
            f"{sys.executable} -m pip install mcp "
            "(or install idios-mcp-server, which pulls it in). "
            "If you meant to use a venv, rerun the doctor with the venv's python.",
        )
    else:
        report(PASS, "mcp package", f"imports in {sys.executable}")


def load_config(path):
    if not os.path.exists(path):
        report(
            FAIL,
            "config file",
            f"{path} does not exist. Check the path, or create the config "
            "(see QUICKSTART for the five required fields).",
        )
        return None
    try:
        with open(path, "r") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        report(
            FAIL,
            "config file",
            f"{path} exists but is not valid JSON: {e}. "
            "Common causes: a trailing comma, missing quotes, or a stray "
            "character from a paste.",
        )
        return None
    if not isinstance(cfg, dict):
        report(
            FAIL,
            "config file",
            f"{path} parses but is not a JSON object. It must be a single "
            "object with the five required keys.",
        )
        return None
    report(PASS, "config file", f"{path} exists and parses as JSON")
    return cfg


def check_required_keys(cfg):
    missing = [k for k in REQUIRED_KEYS if k not in cfg or cfg[k] in (None, "")]
    if missing:
        report(
            FAIL,
            "config keys",
            "missing or empty: " + ", ".join(missing) + ". "
            "All five are required: " + ", ".join(REQUIRED_KEYS) + ".",
        )
        return False
    report(PASS, "config keys", "all five required keys present")
    return True


def check_wallet_binary(path):
    if not os.path.exists(path):
        report(
            FAIL,
            "wallet binary",
            f"{path} does not exist. Point beam_wallet_binary at the "
            "beam-wallet CLI binary from the Beam wallet download.",
        )
        return
    if not os.access(path, os.X_OK):
        report(
            FAIL,
            "wallet binary",
            f"{path} exists but is not executable. Fix: chmod +x {path}",
        )
        return
    report(PASS, "wallet binary", f"{path} exists and is executable")


def check_wasm(path):
    if not os.path.exists(path):
        report(
            FAIL,
            "shader wasm",
            f"{path} does not exist. Point shader_app_file at idios_app.wasm "
            "from the Idios repo.",
        )
        return
    size = os.path.getsize(path)
    if size == 0:
        report(
            FAIL,
            "shader wasm",
            f"{path} exists but is empty (0 bytes). Redownload idios_app.wasm.",
        )
        return
    with open(path, "rb") as f:
        magic = f.read(4)
    if magic != b"\x00asm":
        report(
            FAIL,
            "shader wasm",
            f"{path} does not start with the wasm magic bytes. It is not a "
            "wasm file, likely an HTML error page from a bad download. "
            "Redownload idios_app.wasm from the repo.",
        )
        return
    report(PASS, "shader wasm", f"{path} is a wasm file ({size} bytes)")


def check_wallet_path(path):
    if not os.path.exists(path):
        report(
            FAIL,
            "wallet.db",
            f"{path} does not exist. Point wallet_path at your wallet.db, "
            "created by beam-wallet init.",
        )
        return
    report(PASS, "wallet.db", f"{path} exists")


def check_cid(cid):
    if len(cid) == 64 and all(c in "0123456789abcdefABCDEF" for c in cid):
        report(PASS, "cid", "64 hex characters")
    else:
        report(
            FAIL,
            "cid",
            f"'{cid}' is not a valid contract id. It must be exactly 64 hex "
            f"characters (got {len(cid)}). Copy it verbatim from the Idios "
            "README or your deploy record.",
        )


def check_node(addr):
    if ":" not in addr:
        report(
            FAIL,
            "node address",
            f"'{addr}' has no port. node_addr must be host:port, for example "
            "eu-node01.mainnet.beam.mw:8100",
        )
        return
    host, _, port_s = addr.rpartition(":")
    try:
        port = int(port_s)
    except ValueError:
        report(
            FAIL,
            "node address",
            f"'{port_s}' is not a valid port number in '{addr}'.",
        )
        return
    try:
        with socket.create_connection((host, port), timeout=10):
            pass
    except OSError as e:
        report(
            FAIL,
            "node reachable",
            f"could not open a TCP connection to {host}:{port} within 10 "
            f"seconds ({e}). The node may be down, the address wrong, or "
            "your network blocking it. Try another public node or your own.",
        )
        return
    report(PASS, "node reachable", f"TCP connect to {host}:{port} succeeded")


def check_wallet_pass():
    if os.environ.get("IDIOS_WALLET_PASS"):
        report(PASS, "IDIOS_WALLET_PASS", "set in the environment")
    else:
        report(
            WARN,
            "IDIOS_WALLET_PASS",
            "not set. The server will stop at an interactive password prompt, "
            "which hangs headless launches. Fix: "
            "read -s -p 'Wallet password: ' IDIOS_WALLET_PASS && "
            "export IDIOS_WALLET_PASS && echo",
        )


def main():
    parser = argparse.ArgumentParser(
        description="Preflight check for the Idios MCP server environment."
    )
    parser.add_argument(
        "--config",
        required=True,
        help="path to idios_mcp_config.json",
    )
    args = parser.parse_args()

    check_python_version()
    check_mcp_import()
    cfg = load_config(args.config)

    if cfg is not None and check_required_keys(cfg):
        check_wallet_binary(str(cfg["beam_wallet_binary"]))
        check_wasm(str(cfg["shader_app_file"]))
        check_wallet_path(str(cfg["wallet_path"]))
        check_cid(str(cfg["cid"]))
        check_node(str(cfg["node_addr"]))

    check_wallet_pass()

    fails = results.count(FAIL)
    warns = results.count(WARN)
    passes = results.count(PASS)

    print()
    if fails:
        print(
            f"SUMMARY: {fails} check(s) FAILED, {passes} passed, "
            f"{warns} warning(s). Fix the failures above before launching "
            "the server."
        )
        sys.exit(1)
    elif warns:
        print(
            f"SUMMARY: all {passes} checks passed with {warns} warning(s). "
            "The server should start, read the warning above."
        )
        sys.exit(0)
    else:
        print(f"SUMMARY: all {passes} checks passed. Ready to launch.")
        sys.exit(0)


if __name__ == "__main__":
    main()

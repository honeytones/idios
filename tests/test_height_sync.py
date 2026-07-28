#!/usr/bin/env python3
"""Drive the height fix in both surfaces against a fake beam-wallet that
emits the exact line formats observed on the NUC 28 July 2026."""
import os, sys, types, time, pathlib, importlib.util

REPO = pathlib.Path(__file__).resolve().parent.parent
FAKE = str(pathlib.Path(__file__).resolve().parent / "fake_beam_wallet.py")
CFG = {
    "beam_wallet_binary": FAKE,
    "node_addr": "eu-node01.mainnet.beam.mw:8100",
    "wallet_path": "/tmp/wallet.db",
    "cid": "41ef8be5",
}

fails = []


def check(name, got, want):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name + "  got=" + repr(got))
    if not ok:
        fails.append(name + " wanted " + repr(want))


# ---------- MCP server ----------
# Import the module without running the MCP stdio server.
sys.modules["mcp"] = types.ModuleType("mcp")
fastmcp = types.ModuleType("mcp.server.fastmcp")


class _FakeMCP:
    def __init__(self, *a, **k):
        pass

    def tool(self, *a, **k):
        def deco(f):
            return f
        return deco

    def run(self, *a, **k):
        pass


fastmcp.FastMCP = _FakeMCP
sys.modules["mcp.server"] = types.ModuleType("mcp.server")
sys.modules["mcp.server.fastmcp"] = fastmcp

spec = importlib.util.spec_from_file_location(
    "idios_srv", str(REPO / "idios-mcp-server" / "idios_mcp_server.py"))
srv = importlib.util.module_from_spec(spec)
sys.argv = ["idios_mcp_server.py"]
try:
    spec.loader.exec_module(srv)
except SystemExit:
    pass

srv._cfg = CFG
srv._password = "x"

os.environ["FAKE_MODE"] = "ok"
t0 = time.time()
h, src = srv._node_height()
dt = time.time() - t0
check("mcp: node height is the real tip", h, 3967081)
check("mcp: source is node", src, "node")
print("      listen killed after {:.1f}s (must be well under the 30s timeout)".format(dt))
if dt > 10:
    fails.append("mcp listen did not exit early")

check("mcp: db height parsed off the dotted line", srv._db_height(), 3960294)

out = srv.get_chain_info()
check("mcp: happy path reports the synced tip", "3967081" in out and "synced from the node" in out, True)
check("mcp: happy path never leaks the stale number", "3960294" in out, False)

os.environ["FAKE_MODE"] = "nosync"
out = srv.get_chain_info()
check("mcp: no sync falls back and says UNVERIFIED", "UNVERIFIED" in out and "3960294" in out, True)
check("mcp: no sync refuses expiry advice", "add a margin" in out, False)

os.environ["FAKE_MODE"] = "dead"
out = srv.get_chain_info()
check("mcp: both fail returns an error", "Could not read the current height" in out, True)

# ---------- daemon ----------
spec2 = importlib.util.spec_from_file_location(
    "idios_daemon", str(REPO / "idios-agent-daemon" / "idios_agent_daemon.py"))
dae = importlib.util.module_from_spec(spec2)
sys.argv = ["idios_agent_daemon.py"]
try:
    spec2.loader.exec_module(dae)
except SystemExit:
    pass


class L:
    def warning(self, *a):
        print("      daemon warn: " + (a[0] % a[1:] if len(a) > 1 else a[0]))

    def info(self, *a):
        pass

    def exception(self, *a):
        pass


log = L()
os.environ["FAKE_MODE"] = "ok"
check("daemon: verified height", dae.get_current_height(CFG, "x", log), 3967081)

# Second call inside the cache window must not re-run listen.
os.environ["FAKE_MODE"] = "nosync"
t0 = time.time()
check("daemon: cache serves without re-syncing", dae.get_current_height(CFG, "x", log), 3967081)
if time.time() - t0 > 2:
    fails.append("daemon re-synced inside the cache window")

# Expire the cache: an unsynced wallet must yield None, not a stale number.
dae._height_cache["at"] = time.monotonic() - 10000
dae.SYNC_TIMEOUT_SECONDS = 3
check("daemon: unsynced wallet returns None, never the db value",
      dae.get_current_height(CFG, "x", log), None)

print()
if fails:
    print("FAILURES:")
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all green")

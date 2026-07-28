#!/usr/bin/env python3
import sys, time, os

mode = sys.argv[1] if len(sys.argv) > 1 else ""
behaviour = os.environ.get("FAKE_MODE", "ok")

if mode == "info":
    if behaviour in ("noinfo", "dead"):
        sys.exit(1)
    print('I 2026-07-28.12:37:48.360 removing old log file "/home/tones/beam-cli-new1/logs/wallet_26_07_19_18_21_06.log"')
    print("____Wallet summary____")
    print("Current height............3960294")
    print("Current state ID..........06567c3ccb3a8e3d")
    print("Available................4.12345678 BEAM")
    print("Lock Height...............2240299")
    print("Refresh Height............3862890")
    sys.exit(0)

if mode == "listen":
    print('I 2026-07-28.12:42:15.100 removing old log file "/home/tones/beam-cli-new1/logs/old.log"')
    sys.stdout.flush()
    if behaviour == "dead":
        sys.exit(1)
    if behaviour == "nosync":
        # Node unreachable: connects, never syncs, stays open until killed.
        while True:
            time.sleep(0.2)
    time.sleep(0.3)
    print("I 2026-07-28.12:42:17.870 Sync up to 3967081-5a93a444f9a719f5")
    print("I 2026-07-28.12:42:17.870 Synchronizing with node: 0% (0/1)")
    print("I 2026-07-28.12:42:18.135 Synchronizing with node: 100% (1/1)")
    print("I 2026-07-28.12:42:18.135 Current state is 3967081-5a93a444f9a719f5")
    sys.stdout.flush()
    # listen never exits on its own; the fix must kill it.
    while True:
        time.sleep(0.2)

sys.exit(0)

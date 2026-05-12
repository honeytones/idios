# Legacy: Phase 4 Hypertensor integration

This directory contains the Phase 4 work from April 2026, when Idios was being explored as a settlement layer driven by Hypertensor subnet epoch consensus. That direction has been deferred indefinitely.

The files here form a coherent stack:

- `hypertensor_trigger.py` watches Hypertensor for per-epoch RewardResult events, fires Beam settle or slash based on attestation_percentage
- `idios_consensus.py` extends the Hypertensor Consensus class to fire settlement immediately after each epoch closes, no separate watcher needed
- `idios_job.py` full job lifecycle script for a requester (create, watch, status)
- `idios_payload.py` encrypted payload delivery via Beam private IPFS
- `beam_pubkey_patch.diff` one-line patch to the Hypertensor subnet template that adds a `beam_pubkey` field to ServerInfo
- `requirements.txt` Python dependencies for this stack (heavy: torch, grpcio, substrate-interface, etc.)

None of this is required to use Idios as it ships today. The current contract (v3, live on Beam mainnet) works with any client and worker that can call Beam wallet shaders, no Hypertensor required.

If Phase 4 is ever revived, these are the reference implementations to start from.

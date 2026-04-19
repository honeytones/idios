"""
idios_consensus.py , Idios Consensus Integration (V1)

Extends the Hypertensor Consensus class to fire Beam settlement
immediately after each epoch closes , no polling, no separate watcher.

Usage:
    Replace the Consensus class in server.py with IdiosConsensus:

        from idios_consensus import IdiosConsensus

        self.consensus = IdiosConsensus(
            dht=self.dht,
            subnet_id=self.subnet_id,
            subnet_node_id=self.subnet_node_id,
            record_validator=self.signature_validator,
            hypertensor=self.hypertensor,
            idios_jobs=IDIOS_JOBS,
            start=True,
        )
"""

import logging
import os
import sys
from dataclasses import dataclass
from typing import List, Optional

log = logging.getLogger("idios.consensus")

_idios_repo = os.path.expanduser("~/idios-repo")
if _idios_repo not in sys.path:
    sys.path.insert(0, _idios_repo)

from hypertensor_trigger import (
    JobParams,
    EpochResult,
    ATTESTATION_THRESHOLD,
    get_epoch_result,
    get_epoch_result_from_consensus_data,
    beam_settle,
    beam_slash,
    beam_view_job,
    STATUS_NAMES,
    TERMINAL,
)

_subnet_template = os.path.expanduser("~/subnet-template")
_subnet_proto = os.path.join(_subnet_template, "subnet/proto")
if _subnet_template not in sys.path:
    sys.path.insert(0, _subnet_template)
if _subnet_proto not in sys.path:
    sys.path.insert(0, _subnet_proto)

try:
    from subnet.app.consensus.consensus import Consensus
    from subnet import DHT
    from subnet.dht.validation import RecordValidatorBase
    from subnet.substrate.chain_functions import Hypertensor
except ImportError as e:
    log.error("Could not import from subnet-template: %s", e)
    raise


class IdiosConsensus(Consensus):
    def __init__(self, *, dht, subnet_id, subnet_node_id, record_validator,
                 hypertensor, idios_jobs=None, skip_activate_subnet=False, start=True):
        self._idios_jobs: List[JobParams] = []
        for j in (idios_jobs or []):
            self._idios_jobs.append(JobParams(
                job_id=j["job_id"],
                subnet_id=j.get("subnet_id", subnet_id),
                result_hash=j["result_hash"],
                payment=j["payment"],
                collateral=j.get("collateral", 0),
                asset_id=j.get("asset_id", 0),
            ))
        super().__init__(dht=dht, subnet_id=subnet_id, subnet_node_id=subnet_node_id,
                         record_validator=record_validator, hypertensor=hypertensor,
                         skip_activate_subnet=skip_activate_subnet, start=start)

    async def run_consensus(self, current_epoch: int):
        await super().run_consensus(current_epoch)
        if self._idios_jobs:
            await self.idios_settle_or_slash(current_epoch)

    async def idios_settle_or_slash(self, epoch: int):
        log.info("[Idios] Epoch %d closed , checking %d job(s)", epoch, len(self._idios_jobs))
        if not self._idios_jobs:
            return

        result = get_epoch_result(self.hypertensor, self.subnet_id, epoch)
        if result is None:
            log.info("[Idios] RewardResult not found, trying ConsensusData fallback...")
            result = get_epoch_result_from_consensus_data(self.hypertensor, self.subnet_id, epoch)
        if result is None:
            log.warning("[Idios] Could not determine result for epoch %d , retrying next epoch", epoch)
            return

        log.info("[Idios] Epoch %d: attestation=%d%% → %s", epoch, result.attestation_pct,
                 "PASSED ✅" if result.passed else "FAILED ❌")

        completed = []
        for job in self._idios_jobs:
            try:
                state = beam_view_job(job.job_id)
                status = int(state.get("status", -1))
                if status in TERMINAL:
                    log.info("[Idios] Job %d already %s , removing", job.job_id, STATUS_NAMES.get(status))
                    completed.append(job)
                    continue
                if status != 1:
                    log.info("[Idios] Job %d is %s , skipping", job.job_id, STATUS_NAMES.get(status, status))
                    continue
                if result.passed:
                    txid = beam_settle(job, result.attestation_pct)
                    log.info("[Idios] ✅ Settled , job=%d epoch=%d txid=%s", job.job_id, epoch, txid)
                else:
                    txid = beam_slash(job)
                    log.info("[Idios] ⚡ Slashed , job=%d epoch=%d txid=%s", job.job_id, epoch, txid)
                completed.append(job)
            except Exception as e:
                log.error("[Idios] Error on job %d: %s , will retry next epoch", job.job_id, e)

        for job in completed:
            self._idios_jobs.remove(job)

        log.info("[Idios] %d job(s) remaining", len(self._idios_jobs))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    log.info("Checking imports...")
    log.info("✅ beam_settle, beam_slash imported from hypertensor_trigger")
    log.info("✅ Consensus imported from subnet-template")
    log.info("✅ IdiosConsensus ready")

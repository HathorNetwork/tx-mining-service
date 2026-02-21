# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Simplified mining manager for dev/test environments.

Replaces TxMiningManager by mining transactions synchronously in-process
instead of delegating to external miners via stratum.
"""

import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from hathorlib.exceptions import PushTxFailed
from structlog import get_logger

import txstratum.time
from txstratum.dev.tx_miner import solve_tx
from txstratum.exceptions import JobAlreadyExists, NewJobRefused
from txstratum.jobs import JobStatus, TxJob

if TYPE_CHECKING:
    from hathorlib.client import HathorClient

logger = get_logger()


class DevMiningManager:
    """Mining manager for dev/test environments.

    Mines transactions synchronously in-process instead of using the
    stratum protocol and external miners. The block mining is handled
    separately by the BlockMiner.

    This manager implements the same interface used by the API layer
    so it can be used as a drop-in replacement for TxMiningManager.
    """

    TX_CLEAN_UP_INTERVAL = 300.0  # seconds

    def __init__(self, backend: "HathorClient"):
        self.log = logger.new()
        self.backend = backend
        self.started_at: float = 0
        self.tx_jobs: Dict[bytes, TxJob] = {}
        self.refuse_new_jobs = False

        # Statistics
        self.txs_solved: int = 0
        self.txs_timeout: int = 0
        self.txs_failed: int = 0

    async def start(self) -> None:
        """Start the manager."""
        self.started_at = txstratum.time.time()
        self.log.info("DevMiningManager started")

    async def stop(self) -> None:
        """Stop the manager."""
        self.log.info("DevMiningManager stopped")

    @property
    def uptime(self) -> float:
        if not self.started_at:
            return 0.0
        return txstratum.time.time() - self.started_at

    def get_job(self, uuid: bytes) -> Optional[TxJob]:
        """Return the TxJob related to the uuid."""
        return self.tx_jobs.get(uuid)

    def status(self) -> Dict[Any, Any]:
        """Return status dict compatible with the production manager."""
        return {
            "miners": [],
            "miners_count": 0,
            "total_hashrate_ghs": 0,
            "started_at": self.started_at,
            "txs_solved": self.txs_solved,
            "txs_timeout": self.txs_timeout,
            "blocks_found": 0,
            "uptime": self.uptime,
            "tx_queue": 0,
            "tx_jobs": [job.to_dict() for job in self.tx_jobs.values()],
            "block_template_error": 0,
            "block_template": None,
            "dev_miner": True,
        }

    def add_job(self, job: TxJob) -> bool:
        """Add new tx to be mined. Mines it synchronously in-process."""
        if self.refuse_new_jobs:
            raise NewJobRefused

        if job.uuid in self.tx_jobs:
            prev_job = self.tx_jobs[job.uuid]
            if prev_job.status == JobStatus.TIMEOUT:
                self.tx_jobs.pop(job.uuid)
            else:
                raise JobAlreadyExists

        self.tx_jobs[job.uuid] = job
        job.expected_mining_time = 0
        job.expected_queue_time = 0

        self.log.info("New TxJob (dev-miner)", job_id=job.uuid.hex())

        if job.add_parents:
            asyncio.ensure_future(self._mine_with_parents(job))
        else:
            asyncio.ensure_future(self._mine_job(job))

        return True

    async def _mine_with_parents(self, job: TxJob) -> None:
        """Add parents to the job, then mine it."""
        job.status = JobStatus.GETTING_PARENTS
        try:
            parents: List[bytes] = await self.backend.get_tx_parents()
        except Exception as e:
            job.mark_as_failed(f"Failed to get parents: {e}")
            self.txs_failed += 1
            self._schedule_cleanup(job)
            return

        job.set_parents(parents)
        await self._mine_job(job)

    async def _mine_job(self, job: TxJob) -> None:
        """Mine a transaction job synchronously."""
        job.status = JobStatus.MINING
        job.started_at = txstratum.time.time()
        start = txstratum.time.time()

        tx = job.get_tx()
        tx.timestamp = int(txstratum.time.time())

        if not solve_tx(tx):
            job.mark_as_failed("Failed to solve PoW")
            self.txs_failed += 1
            self._schedule_cleanup(job)
            return

        elapsed_ms = (txstratum.time.time() - start) * 1000
        nonce = tx.get_struct_nonce()

        if job.propagate:
            try:
                await self.backend.push_tx_or_block(bytes(tx))
            except PushTxFailed:
                job.mark_as_failed("Fullnode rejected transaction")
                self.txs_failed += 1
                self._schedule_cleanup(job)
                return

        job.mark_as_solved(nonce=nonce, timestamp=tx.timestamp)
        self.txs_solved += 1

        self.log.info(
            "TxJob solved (dev-miner)",
            job_id=job.uuid.hex(),
            hash=tx.hash_hex,
            weight=tx.weight,
            nonce=tx.nonce,
            time_ms=f"{elapsed_ms:.1f}",
        )
        self._schedule_cleanup(job)

    def cancel_job(self, job: TxJob) -> None:
        """Cancel tx mining job."""
        if job.status in JobStatus.get_after_mining_states():
            raise ValueError("Job has already finished")
        job.status = JobStatus.CANCELLED
        self.tx_jobs.pop(job.uuid)
        self.log.info("TxJob cancelled", job_id=job.uuid.hex())

    def _schedule_cleanup(self, job: TxJob) -> None:
        """Schedule job cleanup."""
        loop = asyncio.get_event_loop()
        loop.call_later(self.TX_CLEAN_UP_INTERVAL, self._cleanup_job, job)

    def _cleanup_job(self, job: TxJob) -> None:
        """Remove job from tracking."""
        self.tx_jobs.pop(job.uuid, None)

    def shutdown(self) -> None:
        """Shutdown the manager."""
        self.refuse_new_jobs = True

    def has_any_miner(self) -> bool:
        """Dev-miner always reports having a miner (itself)."""
        return True

    def has_any_submitted_job_in_period(self, period: int) -> bool:
        """Dev-miner always reports activity."""
        return True

# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Simplified mining manager for dev/test environments.

In production, TxMiningManager (txstratum/manager.py) acts as a stratum server:
it accepts connections from external miners, dispatches transaction and block
jobs to them, and collects solutions. This requires:
  - A running cpuminer (or any stratum-compatible miner)
  - The stratum protocol to coordinate job dispatch and nonce submission
  - A PubSubManager for internal event routing
  - Block template polling and miner assignment logic

DevMiningManager replaces all of that with in-process mining. Transactions are
solved directly (via tx_miner.solve_tx in a thread executor), and the result is
returned through the same job lifecycle that the HTTP API expects.

IMPORTANT: This class implements the same interface consumed by txstratum/api.py
(the HTTP API layer). The API code doesn't know or care whether it's talking to
TxMiningManager or DevMiningManager — it calls add_job(), get_job(), status(),
etc. This is what makes the dev-miner a drop-in replacement: the only change
needed is in cli.py to instantiate DevMiningManager instead of TxMiningManager.
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
    """Drop-in replacement for TxMiningManager that mines transactions in-process.

    Responsibilities split between this class and BlockMiner:
    - DevMiningManager: handles transaction PoW (via solve_tx)
    - BlockMiner: handles block production (separate async loop in block_miner.py)

    The production TxMiningManager handles both in a single class because the
    stratum protocol intermixes block and tx jobs on the same miner connections.
    Here we separate them because there's no stratum — tx mining is request-driven
    (triggered by HTTP submit-job) while block mining is time-driven (periodic loop).
    """

    TX_CLEAN_UP_INTERVAL = 300.0  # seconds

    def __init__(self, backend: "HathorClient"):
        self.log = logger.new()
        self.backend = backend
        self.started_at: float = 0
        self.tx_jobs: Dict[bytes, TxJob] = {}
        self.refuse_new_jobs = False

        # Statistics — same fields as TxMiningManager for API compatibility.
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
        """Return status dict compatible with the production TxMiningManager.

        All stratum-specific fields (miners, hashrate, block_template) are
        hardcoded to empty/zero values since they don't apply in dev-miner
        mode. The `dev_miner: True` flag lets clients detect the mode.
        """
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
        """Add a new transaction to be mined.

        Unlike TxMiningManager (which queues the job and waits for an available
        stratum miner), this kicks off an async task that solves the PoW directly.
        The job transitions through the same states (MINING → DONE/FAILED) that
        the API polling endpoint expects.
        """
        if self.refuse_new_jobs:
            raise NewJobRefused

        # Handle duplicate submissions the same way as the production manager:
        # allow resubmission if the previous attempt timed out, reject otherwise.
        if job.uuid in self.tx_jobs:
            prev_job = self.tx_jobs[job.uuid]
            if prev_job.status == JobStatus.TIMEOUT:
                self.tx_jobs.pop(job.uuid)
            else:
                raise JobAlreadyExists

        self.tx_jobs[job.uuid] = job

        # Expected times are 0 because there's no queue and no stratum latency.
        job.expected_mining_time = 0
        job.expected_queue_time = 0

        self.log.info("New TxJob (dev-miner)", job_id=job.uuid.hex())

        # Kick off mining as an async task. If the job needs parents (i.e. the
        # caller sent add_parents=True), we fetch them from the fullnode first.
        if job.add_parents:
            asyncio.ensure_future(self._mine_with_parents(job))
        else:
            asyncio.ensure_future(self._mine_job(job))

        return True

    async def _mine_with_parents(self, job: TxJob) -> None:
        """Fetch parent transactions from the fullnode, then mine.

        In Hathor, every transaction must reference parent transactions (similar
        to a DAG tip selection). The fullnode provides suitable parents via the
        get_tx_parents API. In production, TxMiningManager does this inline
        before dispatching to a stratum miner.
        """
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
        """Solve PoW for a transaction job.

        The actual nonce iteration runs in a thread executor (via run_in_executor)
        to avoid blocking the async event loop. This is important because the
        BlockMiner loop and HTTP server share the same event loop — a blocking
        solve_tx call would stall block production and API responses.
        """
        job.status = JobStatus.MINING
        job.started_at = txstratum.time.time()
        start = txstratum.time.time()

        tx = job.get_tx()
        # Update timestamp to current time — the fullnode validates that tx
        # timestamps are within an acceptable delta of the current time.
        tx.timestamp = int(txstratum.time.time())

        loop = asyncio.get_event_loop()
        solved = await loop.run_in_executor(None, solve_tx, tx)
        if not solved:
            job.mark_as_failed("Failed to solve PoW")
            self.txs_failed += 1
            self._schedule_cleanup(job)
            return

        elapsed_ms = (txstratum.time.time() - start) * 1000
        nonce = tx.get_struct_nonce()

        # If propagate=True, push the solved tx to the fullnode. This mirrors
        # the production behavior where the stratum miner's solution is
        # automatically pushed to the network.
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
        """Schedule removal of a completed job after TX_CLEAN_UP_INTERVAL.

        Jobs are kept around after completion so the API polling endpoint
        can still return their status. Same behavior as production.
        """
        loop = asyncio.get_event_loop()
        loop.call_later(self.TX_CLEAN_UP_INTERVAL, self._cleanup_job, job)

    def _cleanup_job(self, job: TxJob) -> None:
        """Remove job from tracking."""
        self.tx_jobs.pop(job.uuid, None)

    def shutdown(self) -> None:
        """Shutdown the manager (stop accepting new jobs)."""
        self.refuse_new_jobs = True

    def has_any_miner(self) -> bool:
        """Always True — the dev-miner itself is the miner.

        The health check uses this to determine if the service is operational.
        In production, this checks for connected stratum miners.
        """
        return True

    def has_any_submitted_job_in_period(self, period: int) -> bool:
        """Always True — prevents the health check from reporting unhealthy.

        In production, this checks recent job activity to detect stale services.
        The dev-miner is always "active" by definition.
        """
        return True

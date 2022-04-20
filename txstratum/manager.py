# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
import asyncio
from collections import deque
from typing import TYPE_CHECKING, Any, Deque, Dict, List, Optional

from hathorlib.utils import decode_address
from hathorlib.exceptions import PushTxFailed
from structlog import get_logger

import txstratum.time
from txstratum.exceptions import JobAlreadyExists, NewJobRefused
from txstratum.jobs import JobStatus, MinerBlockJob, MinerJob, MinerTxJob, TxJob
from txstratum.protocol import StratumProtocol
from txstratum.pubsub import PubSubManager, TxMiningEvents
from txstratum.utils import Periodic, calculate_expected_mining_time

if TYPE_CHECKING:
    from hathorlib.client import BlockTemplate, HathorClient  # noqa: F401

logger = get_logger()


class TxMiningManager:
    """Asyncio factory server for Hathor Stratum protocols.

    Events:
        - new block template trigged by a timer
        - new tx to be resolved trigged by an API request
        - nonce found trigged by a miner

    When a miner is ready to mine, it calls `manager.mark_connection_as_ready()`.
    When a miner finds a solution, it calls `manager.submit_solution()`.

    When a new block template is found, manager calls `miner.update_job()`.
    When a new tx arrives, manager calls `miner.update_job()`.
    """

    DEFAULT_BLOCK_TEMPLATE_UPDATE_INTERVAL = 3.0  # seconds
    TX_CLEAN_UP_INTERVAL = 300.0  # seconds

    def __init__(
        self,
        backend: "HathorClient",
        pubsub: "PubSubManager",
        address: Optional[str] = None,
    ):
        """Init TxMiningManager with backend."""
        if address is not None:
            # Validate address. If the address is invalid, it raises an InvalidAddress exception.
            decode_address(address)

        self.log = logger.new()
        self.backend: "HathorClient" = backend
        self.pubsub: "PubSubManager" = pubsub
        self.address = address
        self.started_at: float = 0
        self.tx_jobs: Dict[bytes, TxJob] = {}
        self.tx_queue: Deque[TxJob] = deque()
        self.connections: Dict[str, StratumProtocol] = {}
        self.miners: Dict[str, StratumProtocol] = {}
        self.latest_submitted_block_height: int = -1
        self.block_template_updated_at: float = 0
        self.block_template: Optional["BlockTemplate"] = None
        self.block_template_update_interval = (
            self.DEFAULT_BLOCK_TEMPLATE_UPDATE_INTERVAL
        )
        self.refuse_new_jobs = False

        # Statistics
        self.txs_solved: int = 0
        self.blocks_found: int = 0
        self.txs_timeout: int = 0
        self.block_template_error: int = 0

    def __call__(self) -> StratumProtocol:
        """Return an instance of StratumProtocol for a new connection."""
        protocol = StratumProtocol(self)

        if self.refuse_new_jobs:
            protocol.refuse_new_miners = True
        return protocol

    async def start(self) -> None:
        """Start the manager."""
        self.started_at = txstratum.time.time()
        self.update_block_task = Periodic(
            self.update_block_template, self.block_template_update_interval
        )
        await self.update_block_task.start()

    async def wait_for_block_template(self, interval: float = 0.1) -> None:
        """Wait until receiving a block_template."""
        while self.block_template is None:
            self.log.info("Waiting for first block template...")
            await asyncio.sleep(interval)

    async def stop(self) -> None:
        """Stop the manager."""
        assert self.update_block_task is not None
        await self.update_block_task.stop()

    @property
    def uptime(self) -> float:
        """Live uptime after the manager has been started."""
        if not self.started_at:
            return 0.0
        return txstratum.time.time() - self.started_at

    def add_connection(self, protocol: StratumProtocol) -> None:
        """Add a new connection to the list of connections."""
        self.connections[protocol.miner_id] = protocol

    def remove_connection(self, protocol: StratumProtocol) -> None:
        """Remove a connection from the list of connections."""
        self.connections.pop(protocol.miner_id)
        self.miners.pop(protocol.miner_id, None)

    def shutdown(self) -> None:
        """Tasks to be executed before the service is shut down."""
        self.refuse_new_jobs = True

        for protocol in self.miners.values():
            protocol.refuse_new_miners = True
            protocol.ask_miner_to_reconnect()

    def mark_connection_as_ready(self, protocol: StratumProtocol) -> None:
        """Mark a miner as ready to mine. It is called by StratumProtocol."""
        self.miners[protocol.miner_id] = protocol
        self.update_miner_job(protocol)

    def update_miner_job(
        self, protocol: StratumProtocol, *, clean: bool = False
    ) -> None:
        """Send a new job for a miner."""
        job = self.get_best_job(protocol)
        if job is None:
            # We do not have a job for the miner. We could close the connection,
            # but we do not do it because a TxJob might arrive anytime.
            return

        if isinstance(job, MinerTxJob):
            is_same_job = (
                isinstance(protocol.current_job, MinerTxJob)
                and job.tx_job == protocol.current_job.tx_job
            )

            if not is_same_job:
                # This will tell the miner to abandon its current job and start this immediately.
                # We do not need to force this if the job is the same.
                clean = True

        protocol.update_job(job, clean=clean)

    async def push_block_job(self, job: MinerBlockJob):
        """Pushes a block to the fullnode.

        :param job: The MinerBlockJob wrapping the block
        """
        assert isinstance(job, MinerBlockJob)

        try:
            await self.backend.push_tx_or_block(job.get_data())
        except PushTxFailed:
            self.log.error("Error when submitting block", job=job)
            return

        self.latest_submitted_block_height = job.height
        # XXX Should we stop all other miners from mining?
        asyncio.ensure_future(self.update_block_template())
        self.log.info("Block found", job=job.uuid.hex())
        self.blocks_found += 1

    async def push_tx_job(self, job: MinerTxJob) -> None:
        """Pushes a tx to the fullnode.

        :param job: The MinerTxJob wrapping the tx
        """
        try:
            await self.backend.push_tx_or_block(job.get_data())
        except PushTxFailed:
            assert isinstance(job, MinerTxJob)
            tx_job = job.tx_job

            self.log.error(
                f"Error when propagating tx_job; ",
                tx_job=tx_job.to_dict(),
            )

            raise

        self.log.info("TxJob propagated", tx_job=job.tx_job.to_dict())

    async def submit_block_solution(
        self, job: MinerBlockJob
    ) -> None:
        assert isinstance(job, MinerBlockJob)

        if job.height <= self.latest_submitted_block_height:
            self.log.info(
                (
                    f"Ignoring submission of a block with height {job.height}, "
                    f"because we already submitted a block with height {self.latest_submitted_block_height}."
                ),
                job=job,
            )
            return

        await self.push_block_job(job)

    async def submit_tx_solution(
        self, protocol: StratumProtocol, job: MinerTxJob, nonce: bytes
    ) -> None:
        tx_job = job.tx_job

        if tx_job.status in JobStatus.get_after_mining_states():
            # This can happen if two miners submitted a solution to the same job, for instance
            self.log.debug(
                f"Received solution for a job with status {tx_job.status}",
                job_id=tx_job.uuid.hex(),
            )
            return

        try:
            # Remove from queue.
            self.tx_queue.remove(tx_job)
        except ValueError:
            self.log.warning(
                "Tried removing a job that was not in the queue.",
                job_id=tx_job.uuid.hex(),
            )

        # Schedule to clean it up.
        self.schedule_job_clean_up(tx_job)

        # Mark as solved, stop mining it, and propagate if requested.
        self.stop_mining_tx(tx_job)

        if tx_job.propagate:
            try:
                await self.push_tx_job(job)
            except:
                tx_job.mark_as_failed("Error when propagating the transaction")
                return

        tx_job.mark_as_solved(nonce=nonce, timestamp=job.get_object().timestamp)
        self.txs_solved += 1

        self.log.info(
            "TxJob solved", propagate=tx_job.propagate, tx_job=tx_job.to_dict()
        )
        self.pubsub.emit(
            TxMiningEvents.MANAGER_TX_SOLVED,
            {"tx_job": tx_job, "protocol": protocol},
        )

    def submit_solution(
        self, protocol: StratumProtocol, job: MinerJob, nonce: bytes
    ) -> None:
        """Submit a new solution for a job. It is called by StratumProtocol."""
        if isinstance(job, MinerBlockJob):
            asyncio.ensure_future(self.submit_block_solution(job))
        elif isinstance(job, MinerTxJob):
            asyncio.ensure_future(self.submit_tx_solution(protocol, job, nonce))
        else:
            raise ValueError("Should never get to this point")

    def schedule_job_timeout(self, job: TxJob) -> None:
        """Schedule to have a TxJob marked as timeout."""
        if job._timeout_timer:
            job._timeout_timer.cancel()
        if job.timeout is not None:
            loop = asyncio.get_event_loop()
            job._timeout_timer = loop.call_later(
                job.timeout, self._job_timeout_if_possible, job
            )

    def schedule_job_clean_up(self, job: TxJob) -> None:
        """Schedule to have a TxJob cleaned up."""
        if job._cleanup_timer:
            job._cleanup_timer.cancel()
        loop = asyncio.get_event_loop()
        job._cleanup_timer = loop.call_later(
            self.TX_CLEAN_UP_INTERVAL, self._job_clean_up, job
        )

    async def update_block_template(self) -> None:
        """Update block template. It is periodically called."""
        self.log.debug("Updating block template...")
        try:
            block_template = await self.backend.get_block_template(address=self.address)
        except Exception:
            # XXX What should we do?!
            dt = txstratum.time.time() - self.block_template_updated_at
            if dt > 60:
                self.block_template = None
            self.block_template_error += 1
            self.log.exception("Error updating block template")
            return
        self.block_template_updated_at = txstratum.time.time()
        self.block_template = block_template
        self.log.debug("Block template successfully updated.")
        self.update_miners_block_template()

    def update_miners_block_template(self) -> None:
        """Create and send a new job for each miner."""
        for miner, protocol in self.miners.items():
            if isinstance(protocol.current_job, MinerBlockJob):
                self.update_miner_job(protocol)

    def get_total_hashrate_ghs(self) -> float:
        """Return total hashrate (Gh/s)."""
        return sum(p.hashrate_ghs or 0 for p in self.miners.values())

    def status(self) -> Dict[Any, Any]:
        """Return status dict with useful metrics for use in Status API."""
        miners = [p.status() for p in self.miners.values()]
        total_hashrate_ghs = self.get_total_hashrate_ghs()
        return {
            "miners": miners,
            "miners_count": len(miners),
            "total_hashrate_ghs": total_hashrate_ghs,
            "started_at": self.started_at,
            "txs_solved": self.txs_solved,
            "txs_timeout": self.txs_timeout,
            "blocks_found": self.blocks_found,
            "uptime": self.uptime,
            "tx_queue": len(self.tx_queue),
            "tx_jobs": [job.to_dict() for job in self.tx_jobs.values()],
            "block_template_error": self.block_template_error,
            "block_template": self.block_template.to_dict()
            if self.block_template
            else None,
        }

    def get_job(self, uuid: bytes) -> Optional[TxJob]:
        """Return the TxJob related to the uuid."""
        return self.tx_jobs.get(uuid)

    def add_job(self, job: TxJob) -> bool:
        """Add new tx to be mined."""
        if self.refuse_new_jobs:
            # Will refuse new jobs only while shutting down the service
            raise NewJobRefused

        if job.uuid in self.tx_jobs:
            prev_job = self.tx_jobs[job.uuid]
            if prev_job.status == JobStatus.TIMEOUT:
                self._job_clean_up(prev_job)
            else:
                raise JobAlreadyExists
        self.tx_jobs[job.uuid] = job

        miners_hashrate_ghs = sum(x.hashrate_ghs for x in self.miners.values())
        job.expected_mining_time = calculate_expected_mining_time(
            miners_hashrate_ghs, job.get_weight()
        )

        self.log.info("New TxJob", job=job.to_dict())
        self.pubsub.emit(TxMiningEvents.MANAGER_NEW_TX_JOB, job)

        if job.add_parents:
            asyncio.ensure_future(self.add_parents(job))
        else:
            self.enqueue_tx_job(job)

        return True

    async def add_parents(self, job: TxJob) -> None:
        """Add tx parents to job, then enqueue it."""
        job.status = JobStatus.GETTING_PARENTS
        try:
            parents: List[bytes] = await self.backend.get_tx_parents()
        except Exception as e:
            job.mark_as_failed("Unhandled exception: {}".format(e))
            # Schedule to clean it up.
            self.schedule_job_clean_up(job)
        else:
            job.set_parents(parents)
            self.enqueue_tx_job(job)

    def cancel_job(self, job: TxJob) -> None:
        """Cancel tx mining job."""
        if job.status in JobStatus.get_after_mining_states():
            raise ValueError("Job has already finished")
        job.status = JobStatus.CANCELLED
        self.tx_jobs.pop(job.uuid)
        self.tx_queue.remove(job)
        self.stop_mining_tx(job)
        self.log.info("TxJob cancelled", job=job.to_dict())

    def _job_timeout_if_possible(self, job: TxJob) -> None:
        """Stop mining a tx job because it timeout."""
        if job.status in JobStatus.get_after_mining_states():
            return
        self.log.info("TxJob timeout", job=job.to_dict())
        self.txs_timeout += 1
        self.pubsub.emit(TxMiningEvents.MANAGER_TX_TIMEOUT, job)
        job.status = JobStatus.TIMEOUT
        try:
            self.tx_queue.remove(job)
        except ValueError:
            self.log.error(
                "TxJob timeout but not in queue. This shouldnt happen", job=job
            )
        self.stop_mining_tx(job)
        # Schedule to clean it up.
        self.schedule_job_clean_up(job)

    def enqueue_tx_job(self, job: TxJob) -> None:
        """Enqueue a tx job to be mined."""
        assert job not in self.tx_queue
        job.status = JobStatus.ENQUEUED
        # When the total hashrate is unknown, `expected_mining_time` is set to -1. Thus, we need to
        # skip negative values when calculating the `expected_queue_time`.
        job.expected_queue_time = sum(
            x.expected_mining_time for x in self.tx_queue if x.expected_mining_time > 0
        )
        self.tx_queue.append(job)

        if job.timeout:
            self.schedule_job_timeout(job)

        if len(self.tx_queue) > 1:
            # If the queue is not empty, do nothing.
            return

        for _, protocol in self.miners.items():
            self.update_miner_job(protocol)

    def stop_mining_tx(self, job: TxJob) -> None:
        """Stop mining a tx job."""
        for protocol in self.miners.values():
            if protocol.current_job is not None and not protocol.current_job.is_block:
                assert isinstance(protocol.current_job, MinerTxJob)
                if protocol.current_job.tx_job == job:
                    self.update_miner_job(protocol, clean=True)

    def _job_clean_up(self, job: TxJob) -> None:
        """Clean up tx job. It is scheduled after a tx is solved."""
        self.tx_jobs.pop(job.uuid)
        if job._timeout_timer:
            job._timeout_timer.cancel()
        if job._cleanup_timer:
            job._cleanup_timer.cancel()

    def get_best_job(self, protocol: StratumProtocol) -> Optional[MinerJob]:
        """Return best job for a miner."""
        if len(self.tx_queue) > 0:
            job = self.tx_queue[0]
            assert job.status not in JobStatus.get_after_mining_states()
            if job.status != JobStatus.MINING:
                job.status = JobStatus.MINING
            return MinerTxJob(job)
        if self.block_template is None:
            self.log.error(
                "Cannot generate MinerBlockJob because block_template is empty"
            )
            return None
        return MinerBlockJob(
            data=self.block_template.data, height=self.block_template.height
        )

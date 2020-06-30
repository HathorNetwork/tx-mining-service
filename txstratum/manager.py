# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import asyncio
import time
from collections import deque
from typing import TYPE_CHECKING, Any, Deque, Dict, List, Optional

from structlog import get_logger  # type: ignore
from txstratum.jobs import JobStatus, MinerBlockJob, MinerJob, MinerTxJob
from txstratum.protocol import StratumProtocol
from txstratum.utils import Periodic, calculate_expected_mining_time

if TYPE_CHECKING:
    from txstratum.client import HathorClient, BlockTemplate  # noqa: F401

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

    BLOCK_TEMPLATE_UPDATE_INTERVAL = 3.0
    TX_CLEAN_UP_INTERVAL = 300.0

    def __init__(self, backend: 'HathorClient'):
        """Init TxMiningManager with backend."""
        self.log = logger.new()
        self.backend: 'HathorClient' = backend
        self.started_at: float = 0
        self.tx_jobs: Dict[bytes, MinerTxJob] = {}
        self.tx_queue: Deque[MinerTxJob] = deque()
        self.connections: Dict[str, StratumProtocol] = {}
        self.miners: Dict[str, StratumProtocol] = {}
        self.latest_submitted_block_height: int = -1
        self.block_template_updated_at: float = 0
        self.block_template: Optional['BlockTemplate'] = None

        # Statistics
        self.txs_solved: int = 0
        self.blocks_found: int = 0
        self.txs_timeout: int = 0

    def __call__(self) -> StratumProtocol:
        """Return an instance of StratumProtocol for a new connection."""
        return StratumProtocol(self)

    async def start(self) -> None:
        """Start the manager."""
        self.started_at = time.time()
        await self.update_block_template()
        self.update_block_task = Periodic(self.update_block_template, self.BLOCK_TEMPLATE_UPDATE_INTERVAL)
        await self.update_block_task.start()

    async def stop(self) -> None:
        """Stop the manager."""
        assert self.update_block_task is not None
        await self.update_block_task.stop()

    @property
    def uptime(self) -> float:
        """Live uptime after the manager has been started."""
        if not self.started_at:
            return 0.0
        return time.time() - self.started_at

    def add_connection(self, protocol: StratumProtocol) -> None:
        """Add a new connection to the list of connections."""
        self.connections[protocol.miner_id] = protocol

    def remove_connection(self, protocol: StratumProtocol) -> None:
        """Remove a connection from the list of connections."""
        self.connections.pop(protocol.miner_id)
        self.miners.pop(protocol.miner_id, None)

    def mark_connection_as_ready(self, protocol: StratumProtocol) -> None:
        """Mark a miner as ready to mine. It is called by StratumProtocol."""
        self.miners[protocol.miner_id] = protocol
        self.update_miner_job(protocol)

    def update_miner_job(self, protocol: StratumProtocol) -> None:
        """Send a new job for a miner."""
        job = self.get_best_job(protocol)
        # TODO Set clean based on old job vs. new job
        clean = isinstance(job, MinerTxJob)
        protocol.update_job(job, clean=clean)

    def submit_solution(self, protocol: StratumProtocol, job: MinerJob, nonce: bytes) -> None:
        """Submit a new solution for a job. It is called by StratumProtocol."""
        if job.is_block:
            assert isinstance(job, MinerBlockJob)
            if job.height <= self.latest_submitted_block_height:
                return
            self.latest_submitted_block_height = job.height
            asyncio.ensure_future(self.backend.push_tx_or_block(job.get_data()))
            # XXX Should we stop all other miners from mining?
            asyncio.ensure_future(self.update_block_template())
            self.log.info('Block found', job=job.uuid)
            self.blocks_found += 1

        else:
            assert isinstance(job, MinerTxJob)
            # Remove from queue.
            job2 = self.tx_queue.popleft()
            assert job is job2
            # Schedule to clean it up.
            loop = asyncio.get_event_loop()
            loop.call_later(self.TX_CLEAN_UP_INTERVAL, self._job_clean_up, job)
            # Mark as solved, stop mining it, and propagate if requested.
            job.mark_as_solved(nonce)
            self.stop_mining_tx(job)
            if job.propagate:
                asyncio.ensure_future(self.backend.push_tx_or_block(job.get_data()))
            self.log.info('TxJob solved', propagate=job.propagate, job=job.to_dict())
            self.txs_solved += 1

    async def update_block_template(self) -> None:
        """Update block template. It is periodically called."""
        self.log.debug('Updating block template...')
        try:
            block_template = await self.backend.get_block_template()
        except Exception:
            # XXX What should we do?!
            self.block_template = None
            return
        self.block_template_updated_at = time.time()
        self.block_template = block_template
        self.log.debug('Block template successfully updated.')
        self.update_miners_block_template()

    def update_miners_block_template(self) -> None:
        """Create and send a new job for each miner."""
        for miner, protocol in self.miners.items():
            if isinstance(protocol.current_job, MinerBlockJob):
                self.update_miner_job(protocol)

    def status(self) -> Dict[Any, Any]:
        """Return status dict with useful metrics for use in Status API."""
        miners = [p.status() for p in self.miners.values()]
        total_hashrate_ghs = sum(p.hashrate_ghs or 0 for p in self.miners.values())
        return {
            'miners': miners,
            'total_hashrate_ghs': total_hashrate_ghs,
            'started_at': self.started_at,
            'txs_solved': self.txs_solved,
            'txs_timeout': self.txs_timeout,
            'blocks_found': self.blocks_found,
            'uptime': self.uptime,
            'tx_queue': len(self.tx_queue),
            'tx_jobs': [job.to_dict() for job in self.tx_jobs.values()],
            'block_template': self.block_template.to_dict() if self.block_template else None,
        }

    def get_job(self, uuid: bytes) -> Optional[MinerTxJob]:
        """Return the MinerTxJob related to the uuid."""
        return self.tx_jobs.get(uuid)

    def add_job(self, job: MinerTxJob) -> bool:
        """Add new tx to be mined."""
        if job.uuid in self.tx_jobs:
            return False
        self.tx_jobs[job.uuid] = job

        miners_hashrate_ghs = sum(x.hashrate_ghs for x in self.miners.values())
        job.expected_mining_time = calculate_expected_mining_time(miners_hashrate_ghs, job.get_weight())

        self.log.info('New TxJob', job=job.to_dict())

        if job.add_parents:
            asyncio.ensure_future(self.add_parents(job))
        else:
            self.enqueue_tx_job(job)
        return True

    async def add_parents(self, job: MinerTxJob) -> None:
        """Add tx parents to job, then enqueue it."""
        job.status = JobStatus.GETTING_PARENTS
        try:
            parents: List[bytes] = await self.backend.get_tx_parents()
        except Exception as e:
            job.status = JobStatus.FAILED
            job.message = 'Unhandled exception: {}'.format(e)
            # Schedule to clean it up.
            loop = asyncio.get_event_loop()
            loop.call_later(self.TX_CLEAN_UP_INTERVAL, self._job_clean_up, job)
        else:
            job.set_parents(parents)
            self.enqueue_tx_job(job)

    def cancel_job(self, job: MinerTxJob) -> None:
        """Cancel tx mining job."""
        job.status = JobStatus.CANCELLED
        self.tx_jobs.pop(job.uuid)
        self.tx_queue.remove(job)
        self.stop_mining_tx(job)
        self.log.info('TxJob cancelled', job=job.to_dict())

    def _job_timeout_if_possible(self, job: MinerTxJob) -> None:
        """Stop mining a tx job because it timeout."""
        if job.status != JobStatus.MINING:
            return
        job.status = JobStatus.TIMEOUT
        self.tx_queue.remove(job)
        self.stop_mining_tx(job)
        self.log.info('TxJob timeout', job=job.to_dict())
        self.txs_timeout += 1
        # Schedule to clean it up.
        loop = asyncio.get_event_loop()
        loop.call_later(self.TX_CLEAN_UP_INTERVAL, self._job_clean_up, job)

    def enqueue_tx_job(self, job: MinerTxJob) -> None:
        """Enqueue a tx job to be mined."""
        assert job not in self.tx_queue
        job.status = JobStatus.ENQUEUED
        job.expected_queue_time = sum(x.expected_mining_time for x in self.tx_queue)
        self.tx_queue.append(job)

        if len(self.tx_queue) > 1:
            # If the queue is not empty, do nothing.
            return

        for _, protocol in self.miners.items():
            self.update_miner_job(protocol)

    def stop_mining_tx(self, job: MinerTxJob) -> None:
        """Stop mining a tx job."""
        for protocol in self.miners.values():
            if protocol.current_job == job:
                self.update_miner_job(protocol)

    def _job_clean_up(self, job: MinerTxJob) -> None:
        """Clean up tx job. It is scheduled after a tx is solved."""
        self.tx_jobs.pop(job.uuid)

    def get_best_job(self, protocol: StratumProtocol) -> MinerJob:
        """Return best job for a miner."""
        if len(self.tx_queue) > 0:
            job = self.tx_queue[0]
            assert job.status not in JobStatus.get_after_mining_states()
            if job.status != JobStatus.MINING:
                job.status = JobStatus.MINING
                if job.timeout:
                    loop = asyncio.get_event_loop()
                    loop.call_later(job.timeout, self._job_timeout_if_possible, job)
            return job
        assert self.block_template is not None
        return MinerBlockJob(data=self.block_template.data, height=self.block_template.height)

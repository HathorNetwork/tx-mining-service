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
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import aiohttp
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

    # Upper bound for the timestamp cache. A tx's timestamp is covered by its
    # hash, so a hash->timestamp entry can never go stale whatever happens to
    # the tx afterwards; the bound only keeps the dict from growing over a long
    # run. Note these hashes are mostly DAG tips — the least confirmed vertices
    # in the graph — so confirmation state is deliberately not part of this.
    PARENT_TIMESTAMP_CACHE_SIZE = 1024

    def __init__(self, backend: "HathorClient"):
        """Initialize the dev mining manager."""
        self.log = logger.new()
        self.backend = backend
        self.started_at: float = 0
        self.tx_jobs: Dict[bytes, TxJob] = {}
        self._tasks: Dict[bytes, asyncio.Task[None]] = {}
        self.refuse_new_jobs = False
        self._parent_timestamps: "OrderedDict[bytes, int]" = OrderedDict()

        # Statistics — same fields as TxMiningManager for API compatibility.
        self.txs_solved: int = 0
        self.txs_timeout: int = 0
        self.txs_failed: int = 0

    async def start(self) -> None:
        """Start the manager."""
        if self.started_at:
            return
        self.started_at = txstratum.time.time()
        self.log.info("DevMiningManager started")

    async def stop(self) -> None:
        """Stop the manager."""
        if not self.started_at:
            return
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        self.started_at = 0
        self.log.info("DevMiningManager stopped")

    @property
    def uptime(self) -> float:
        """Return the manager uptime in seconds."""
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
            # Block mining is handled by BlockMiner (separate component);
            # this manager only tracks transaction mining statistics.
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
            task = asyncio.create_task(self._mine_with_parents(job))
        else:
            task = asyncio.create_task(self._mine_job(job))
        self._tasks[job.uuid] = task
        task.add_done_callback(lambda _: self._tasks.pop(job.uuid, None))

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
            job.mark_as_failed(f"Failed to get parents ({type(e).__name__}): {e}")
            self.txs_failed += 1
            self._schedule_cleanup(job)
            return

        job.set_parents(parents)
        await self._mine_job(job)

    async def _fetch_tx_timestamp(self, tx_hash: bytes) -> Optional[int]:
        """Read one transaction's timestamp from the fullnode, or None.

        hathorlib's HathorClient has no "get transaction by hash", and this fix
        is deliberately confined to this repo, so the client's session and URL
        builder are used directly. Both are private, which is exactly why this
        is the only place that touches them: if hathorlib later grows a proper
        accessor (or the parents endpoint starts returning timestamps, as
        HathorNetwork/tx-mining-service#172 sketches), this body is the only
        thing that has to change.

        Returns None on any failure — a timestamp we cannot read is one we
        cannot clamp against, and degrading to plain wall-clock time is far
        better than failing the job. That promise has to hold absolutely: this
        runs inside the mining task, so an exception escaping here strands the
        job in MINING with no cleanup scheduled. Hence both the private-attribute
        access and the payload parsing below are guarded.
        """
        cached = self._parent_timestamps.get(tx_hash)
        if cached is not None:
            self._parent_timestamps.move_to_end(tx_hash)
            return cached

        try:
            # Inside the try on purpose: these attributes are private, so a
            # rename in a future hathorlib degrades to wall-clock time with a
            # warning instead of stranding every job that needs a parent.
            session = self.backend._session
            if session is None:
                return None
            url = self.backend._get_url("transaction")
            # An explicit timeout: the session carries aiohttp's 5-minute
            # default, and this manager never arms a job timeout, so a stalled
            # fullnode would otherwise hold the job in MINING for that long.
            # A lookup slower than this is worthless anyway — the clock it is
            # being compared against has already moved on.
            async with session.get(
                url,
                params={"id": tx_hash.hex()},
                timeout=aiohttp.ClientTimeout(total=2),
            ) as resp:
                if resp.status != 200:
                    self.log.warn(
                        "Parent timestamp lookup failed",
                        parent=tx_hash.hex(),
                        status=resp.status,
                    )
                    return None
                data = await resp.json()
        except Exception as e:
            self.log.warn(
                "Could not read parent timestamp",
                parent=tx_hash.hex(),
                error=f"{type(e).__name__}: {e}",
            )
            return None

        # The payload is not assumed well-formed. A JSON array, a null, or a
        # non-object "tx" must degrade like any other unreadable predecessor
        # rather than raise, so every access below is type-checked first.
        if not isinstance(data, dict):
            self.log.warn("Malformed transaction payload", parent=tx_hash.hex())
            return None
        if not data.get("success"):
            # Expected: the fullnode has not indexed this tx yet. Not logged —
            # the clamp exists for txs broadcast moments ago, so this is the
            # ordinary case rather than a fault.
            return None
        tx_data = data.get("tx")
        timestamp = tx_data.get("timestamp") if isinstance(tx_data, dict) else None
        if not isinstance(timestamp, int):
            self.log.warn("Malformed transaction payload", parent=tx_hash.hex())
            return None

        self._parent_timestamps[tx_hash] = timestamp
        if len(self._parent_timestamps) > self.PARENT_TIMESTAMP_CACHE_SIZE:
            self._parent_timestamps.popitem(last=False)
        return timestamp

    async def _get_timestamp_for(self, predecessors: List[bytes]) -> int:
        """Return a timestamp strictly greater than every predecessor's.

        The fullnode enforces this twice, in two separate verifiers, and both
        must hold: a tx must beat each of its parents (`vertex_verifier`) and
        each tx it spends (`transaction_verifier`). Neither set contains the
        other — parents are drawn from the current tips, while a spent tx may
        have been confirmed long ago and so can never be selected as a parent.
        Pass both.

        Wall-clock time alone does not satisfy either rule at 1-second
        granularity: two txs in the same second, where one precedes the other,
        produce a rejection the client cannot prevent or repair, since both the
        timestamp and the parents are assigned here, under the nonce.

        The lead over wall-clock grows only along a chain of same-second
        predecessors, at most one second per link, against hathor-core's
        300s MAX_FUTURE_TIMESTAMP_ALLOWED. Sustained chaining above one tx per
        second could in principle reach that ceiling; measured workloads stay
        at a lead of one second. See HathorNetwork/tx-mining-service#172 for
        the full analysis and why deferring is not worth its livelock risk.
        """
        # dict.fromkeys dedups while preserving order: several inputs commonly
        # spend the same tx, and a parent is often an input too.
        timestamps: List[Optional[int]] = list(
            await asyncio.gather(
                *(self._fetch_tx_timestamp(h) for h in dict.fromkeys(predecessors))
            )
        )
        # Read the clock after the lookups, not before — they are network I/O.
        now = int(txstratum.time.time())
        return max([now, *(ts + 1 for ts in timestamps if ts is not None)])

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
        # Stamp above everything the fullnode compares this tx against: its DAG
        # parents and every tx it spends. Both are checked, in separate
        # verifiers, and a spent tx is frequently not among the parents.
        tx.timestamp = await self._get_timestamp_for(
            [*tx.parents, *(txin.tx_id for txin in tx.inputs)]
        )

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
            except Exception as e:
                job.mark_as_failed(
                    f"Fullnode rejected transaction ({type(e).__name__}): {e}"
                )
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
        task = self._tasks.pop(job.uuid, None)
        if task is not None:
            task.cancel()
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
        """Return True; the dev-miner itself is the miner.

        The health check uses this to determine if the service is operational.
        In production, this checks for connected stratum miners.
        """
        return True

    def has_any_submitted_job_in_period(self, period: int) -> bool:
        """Return True to indicate the dev-miner is always active.

        In production, this checks recent job activity to detect stale services.
        The dev-miner is always "active" by definition.
        """
        return True

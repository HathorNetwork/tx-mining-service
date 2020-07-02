# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
import asyncio
import time
import uuid
from functools import reduce
from math import log2
from typing import TYPE_CHECKING, Any, Callable, Dict, List, NamedTuple, Optional, cast

from structlog import get_logger  # type: ignore

from txstratum.commons import sum_weights
from txstratum.commons.utils import InvalidAddress, decode_address
from txstratum.jobs import MinerBlockJob
from txstratum.utils import JSONRPCError, JSONRPCId, JSONRPCProtocol, MaxSizeOrderedDict, Periodic

if TYPE_CHECKING:
    from txstratum.jobs import MinerJob
    from txstratum.manager import TxMiningManager  # noqa: F401

logger = get_logger()


class SubmittedWork(NamedTuple):
    """Submitted work by the miner."""

    timestamp: float
    weight: float


class StratumProtocol(JSONRPCProtocol):
    """Implementation of Stratum Protocol.

    Specification: https://en.bitcoin.it/wiki/Stratum_mining_protocol
    """

    ESTIMATOR_LOOP_INTERVAL = 30  # in seconds, "frequency" that the function that updates the estimator will be called
    ESTIMATOR_WINDOW_INTERVAL = 60 * 15  # in seconds, size of the window to use for estimating miner's hashrate
    TARGET_JOB_TIME = 15  # in seconds, adjust difficulty so jobs take this long
    JOB_UPDATE_INTERVAL = 2  # in seconds, to update the timestamp of the job

    MIN_WEIGHT = 20  # minimum "difficulty" to assign to jobs
    MAX_WEIGHT = 60  # maximum "difficulty" to assign to jobs
    INITIAL_WEIGHT = 30  # initial "difficulty" to assign to jobs, can raise or drop based on solvetimes
    MAX_JOBS = 1000  # maximum number of jobs to keep in memory

    INVALID_ADDRESS = JSONRPCError(22, 'Address to send mined funds is invalid')
    INVALID_SOLUTION = JSONRPCError(30, 'Invalid solution')
    STALE_JOB = JSONRPCError(31, 'Stale job submitted')
    JOB_NOT_FOUND = JSONRPCError(32, 'Job not found')

    def __init__(self, manager: 'TxMiningManager'):
        """Init StratumProcol to receive a new connection."""
        self.log = logger.new()
        self.manager: 'TxMiningManager' = manager
        self.miner_id: str = str(uuid.uuid4())
        self.miner_address: Optional[bytes] = None
        self.miner_address_str: Optional[str] = None

        self.jobs: MaxSizeOrderedDict[bytes, 'MinerJob'] = MaxSizeOrderedDict(max=self.MAX_JOBS)
        self.current_job: Optional['MinerJob'] = None
        self.current_weight: float = self.INITIAL_WEIGHT
        self.hashrate_ghs: float = 0.0

        self._submitted_work: List[SubmittedWork] = []

        self._authorized: bool = False
        self._subscribed: bool = False
        self.started_at: float = 0.0
        self.estimator_task: Optional[Periodic] = None
        self.refresh_job_task: Optional[Periodic] = None

        # For testing only.
        self._update_job_timestamp: bool = True

        self.last_submit_at: float = 0.0
        self.completed_jobs: int = 0
        self.txs_solved: int = 0
        self.blocks_found: int = 0
        self.supported_methods: Dict[str, Callable[[Any, JSONRPCId], None]] = {
            'subscribe': self.method_subscribe,
            'mining.subscribe': self.method_subscribe,
            'login': self.method_subscribe,

            'authorize': self.method_authorize,
            'mining.authorize': self.method_authorize,

            'submit': self.method_submit,
            'mining.submit': self.method_submit,

            'configure': self.method_configure,
            'mining.configure': self.method_configure,

            'multi_version': self.method_multi_version,
            'mining.multi_version': self.method_multi_version,

            'mining.extranonce.subscribe': self.method_extranonce_subscribe,
        }

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Set things up after a new connection is made.

        See: https://docs.python.org/3/library/asyncio-protocol.html#asyncio.BaseProtocol.connection_made
        """
        self.transport = cast(asyncio.Transport, transport)
        self.manager.add_connection(self)
        self.log = self.log.bind(miner_id=self.miner_id)
        self.log.debug('connection made')

    def connection_lost(self, exc: Optional[Exception]) -> None:
        """Clean up after a connection is lost.

        See: https://docs.python.org/3/library/asyncio-protocol.html#asyncio.BaseProtocol.connection_lost
        """
        self.log.debug('connection lost', exc=exc)
        if self._subscribed:
            self.log.info('Miner exited')
        assert self.miner_id is not None
        self.manager.remove_connection(self)
        if self.estimator_task:
            asyncio.ensure_future(self.estimator_task.stop())
        if self.refresh_job_task:
            asyncio.ensure_future(self.refresh_job_task.stop())

    def start_periodic_tasks(self) -> None:
        """Start periodic tasks."""
        if self.estimator_task is None:
            self.last_reduced = time.time()
            # Start estimator periodic task.
            self.estimator_task = Periodic(self.estimator_loop, self.ESTIMATOR_LOOP_INTERVAL)
            asyncio.ensure_future(self.estimator_task.start())
            # Start fresh job periodic task.
            self.refresh_job_task = Periodic(self.refresh_job, self.JOB_UPDATE_INTERVAL)
            asyncio.ensure_future(self.refresh_job_task.start())

    def method_extranonce_subscribe(self, params: Any, msgid: JSONRPCId) -> None:
        """Handle extra-nonce request from JSONRPC."""
        self.send_result(msgid, True)

    def method_multi_version(self, params: Any, msgid: JSONRPCId) -> None:
        """Handle multi-version request from JSONRPC."""
        self.send_result(msgid, True)

    def method_subscribe(self, params: Any, msgid: JSONRPCId) -> None:
        """Handle subscribe request from JSONRPC."""
        if params and 'address' in params and params['address'] is not None:
            try:
                address = params['address']
                self.miner_address = decode_address(address)
                self.miner_address_str = address
            except InvalidAddress:
                self.log.debug('Miner with invalid address', address=address)
                self.send_error(msgid, self.INVALID_ADDRESS)
                self.transport.close()
                return
        self._subscribed = True
        self.send_result('ok', msgid)
        self.log.info('Miner subscribed', address=self.miner_address_str)
        self.start_if_ready()

    def method_authorize(self, params: Any, msgid: JSONRPCId) -> None:
        """Handle authorize request from JSONRPC."""
        self._authorized = True
        self.log.info('Miner authorized')
        self.start_if_ready()

    def method_submit(self, params: Any, msgid: JSONRPCId) -> None:
        """Handle submit request from JSONRPC.

        Args:
            params: a dict containing `job_id` and `nonce`
        """
        self.log.debug('handle submit', msgid=msgid, params=params)
        if 'job_id' not in params or 'nonce' not in params:
            return self.send_error(msgid, self.INVALID_PARAMS, {'params': params, 'required': ['job_id', 'nonce']})

        try:
            job_id = bytes.fromhex(params['job_id'])
        except ValueError:
            return self.send_error(msgid, self.INVALID_PARAMS, 'invalid job_id format')

        try:
            nonce = int(params['nonce'], 16)
        except ValueError:
            return self.send_error(msgid, self.INVALID_PARAMS, 'invalid nonce')

        job = self.jobs.get(job_id, None)

        if job is None:
            # XXX Should we look into the job history? It may be necessary if the job was not marked as clean.
            return self.send_error(msgid, self.JOB_NOT_FOUND)

        if job is not self.current_job or job.submitted_at is not None:
            return self.send_error(msgid, self.STALE_JOB, {
                'current_job': self.current_job and self.current_job.uuid.hex(),
                'job_id': job_id.hex()
            })

        obj = job.get_object()
        obj.nonce = nonce
        obj.update_hash()
        if not obj.verify_pow(override_weight=job.share_weight):
            self.log.error('Invalid share weight', uuid=job.uuid.hex(), share_weight=job.share_weight)
            return self.send_error(msgid, self.INVALID_SOLUTION)

        now = time.time()
        self.completed_jobs += 1
        job.submitted_at = now
        self.last_submit_at = now
        self._submitted_work.append(SubmittedWork(job.submitted_at, job.share_weight))
        self.send_result(msgid, 'ok')

        # XXX Is it a good rule when mining txs?
        # Too many jobs too fast, increase difficulty out of caution (more than 10 submits within the last 10s)
        if sum(1 for t, _ in self._submitted_work if now - t < 10) > 10:
            # Doubles the difficulty
            self.set_current_weight(self.current_weight + 1)

        if obj.verify_pow():
            self.manager.submit_solution(self, job, obj.get_struct_nonce())
            if obj.is_block:
                self.blocks_found += 1
            else:
                self.txs_solved += 1

        else:
            # XXX TODO Should we send a new job?
            pass

    def method_configure(self, params: Any, msgid: JSONRPCId) -> None:
        """Handle stratum-extensions configuration from JSONRPC.

        See: https://github.com/slushpool/stratumprotocol/blob/master/stratum-extensions.mediawiki
        """
        self.log.debug('handle configure', msgid=msgid, params=params)
        exts, exts_params = params
        res: Dict[str, Any] = {ext: False for ext in exts}

        # TODO
        # if 'minimum-difficulty' in exts:
        #     self.min_difficulty = int(exts_params['minimum-difficulty.value'])
        #     res['minimum-difficulty'] = True

        self.send_result(msgid, res)

    async def estimator_loop(self) -> None:
        """Estimate miner's hashrate. It is automatically called."""
        if not self.current_job:
            # not ready yet, skip this run
            return
        now = time.time()
        # remove old entries
        self._submitted_work = [x for x in self._submitted_work if now - x.timestamp < self.ESTIMATOR_WINDOW_INTERVAL]
        # too little jobs, reduce difficulty
        if len(self._submitted_work) == 0:
            self.set_current_weight(self.current_weight - 0.5)
            return
        # otherwise, estimate the hashrate, and aim for a difficulty that approximates the target job time
        # window size
        if len(self._submitted_work) <= 1:
            return
        delta = self._submitted_work[-1].timestamp - self._submitted_work[0].timestamp
        # total logwork (considering the highest hash only)
        logwork = reduce(lambda w1, w2: sum_weights(w1, w2), (w for (_, w) in self._submitted_work))
        # calculate hashrate in TH/s
        self.hashrate_ghs = 2**(logwork - log2(delta) - 30)
        self.set_current_weight(logwork - log2(delta) + log2(self.TARGET_JOB_TIME))

    def set_current_weight(self, weight: float) -> None:
        """Set current weight and update miner's job."""
        self.current_weight = max(self.MIN_WEIGHT, weight)
        assert self.current_job is not None
        self.update_job(self.current_job, clean=False)

    def is_ready(self) -> bool:
        """Return True if miner is ready to mine."""
        if self._subscribed:
            return True
        return False

    def status(self) -> Dict[Any, Any]:
        """Return dict with useful metrics."""
        job_type = None
        if self.current_job is not None:
            job_type = 'block' if self.current_job.is_block else 'transaction'

        return {
            'id': self.miner_id,
            'hashrate_ghs': self.hashrate_ghs,
            'weight': self.current_weight,
            'miner_address': self.miner_address_str,
            'job_type': job_type,
            'started_at': self.started_at,
            'last_submit_at': self.last_submit_at,
            'uptime': self.uptime,
            'completed_jobs': self.completed_jobs,
            'txs_solved': self.txs_solved,
            'blocks_found': self.blocks_found,
        }

    @property
    def uptime(self) -> float:
        """Live uptime after the miner is ready."""
        if not self.started_at:
            return 0.0
        return time.time() - self.started_at

    def start_if_ready(self) -> None:
        """Start mining if it is ready."""
        if not self.is_ready():
            return
        if self.current_job is not None:
            return
        self.started_at = time.time()
        self.start_periodic_tasks()
        self.manager.mark_connection_as_ready(self)

    def is_mining_block(self) -> bool:
        """Return True if the miner is mining a block."""
        if self.current_job is None:
            return False
        return self.current_job.is_block

    async def refresh_job(self) -> None:
        """Refresh miner's job, updating timestamp."""
        assert self.current_job is not None
        self.update_job(self.current_job, clean=False)

    def update_job(self, job: 'MinerJob', *, clean: bool) -> None:
        """Update miner's job. It is called by the manager."""
        # Set job's share weight
        job.share_weight = min(self.current_weight, job.get_weight())

        # Set miner address.
        if job.is_block and self.miner_address:
            assert isinstance(job, MinerBlockJob)
            job.set_mining_address(self.miner_address)

        # Update timestamp.
        if self._update_job_timestamp:
            job.update_timestamp()

        # Add job to the job list and set it as current job
        self.current_job = job
        self.jobs[job.uuid] = job

        # Send new job to the miner.
        job_data = {
            'data': job.get_header_without_nonce().hex(),
            'job_id': job.uuid.hex(),
            'nonce_size': job.get_nonce_size(),
            'weight': job.share_weight,
            'clean': clean,
        }
        self.log.debug('Update job', job=job_data, block=job.get_data().hex())
        self.send_request('job', job_data, msgid=None)

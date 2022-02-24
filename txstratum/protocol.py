# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
import asyncio
import uuid
from math import log2
from typing import TYPE_CHECKING, Any, Callable, Dict, List, NamedTuple, Optional, cast

from hathorlib import sum_weights
from hathorlib.conf import HathorSettings
from hathorlib.exceptions import InvalidAddress
from hathorlib.scripts import binary_to_int
from hathorlib.utils import decode_address
from structlog import get_logger

import txstratum.time
from txstratum.jobs import MinerBlockJob
from txstratum.pubsub import TxMiningEvents
from txstratum.utils import (
    JSONRPCError,
    JSONRPCId,
    JSONRPCProtocol,
    MaxSizeOrderedDict,
    Periodic,
)

if TYPE_CHECKING:
    from txstratum.jobs import MinerJob
    from txstratum.manager import TxMiningManager  # noqa: F401

logger = get_logger()
settings = HathorSettings()
VALID_FIRST_BYTE = {
    binary_to_int(settings.P2PKH_VERSION_BYTE),
    binary_to_int(settings.MULTISIG_VERSION_BYTE),
}


class SubmittedWork(NamedTuple):
    """Submitted work by the miner."""

    timestamp: float
    weight: float
    dt: float


class MessageInTransit(NamedTuple):
    """Message that is waiting response from the client."""

    id: int  # Some miners, like cgminer, only support integer ids
    method: str
    timeout: float


class StratumProtocol(JSONRPCProtocol):
    """Implementation of Stratum Protocol.

    Specification: https://en.bitcoin.it/wiki/Stratum_mining_protocol
    """

    ESTIMATOR_LOOP_INTERVAL = 15  # in seconds, "frequency" that the function that updates the estimator will be called
    ESTIMATOR_WINDOW_INTERVAL = (
        60 * 15
    )  # in seconds, size of the window to use for estimating miner's hashrate
    TARGET_JOB_TIME = 15  # in seconds, adjust difficulty so jobs take this long
    JOB_UPDATE_INTERVAL = 2  # in seconds, to update the timestamp of the job
    MESSAGES_TIMEOUT_INTERVAL = (
        5  # in seconds, the interval to clear timed out messages
    )

    MIN_WEIGHT = 20.0  # minimum "difficulty" to assign to jobs
    MAX_WEIGHT = 60.0  # maximum "difficulty" to assign to jobs
    INITIAL_WEIGHT = 30.0  # initial "difficulty" to assign to jobs, can raise or drop based on solvetimes
    MAX_JOBS = 1000  # maximum number of jobs to keep in memory

    MESSAGE_TIMEOUT = 30  # in seconds, timeout for messages that are not answered

    INVALID_ADDRESS = JSONRPCError(22, "Address to send mined funds is invalid")
    INVALID_SOLUTION = JSONRPCError(30, "Invalid solution")
    STALE_JOB = JSONRPCError(31, "Stale job submitted")
    JOB_NOT_FOUND = JSONRPCError(32, "Job not found")
    SERVICE_SHUTTING_DOWN = JSONRPCError(40, "Service shutting down")

    def __init__(self, manager: "TxMiningManager"):
        """Init StratumProcol to receive a new connection."""
        self.log = logger.new()
        self.manager: "TxMiningManager" = manager
        self.miner_id: str = str(uuid.uuid4())
        self.miner_address: Optional[bytes] = None
        self.miner_address_str: Optional[str] = None
        self.miner_version: str = "unknown"

        self.jobs: MaxSizeOrderedDict[bytes, "MinerJob"] = MaxSizeOrderedDict(
            max=self.MAX_JOBS
        )
        self.current_job: Optional["MinerJob"] = None
        self.current_weight: float = self.INITIAL_WEIGHT
        self.hashrate_ghs: float = 0.0

        self._submitted_work: List[SubmittedWork] = []

        self._authorized: bool = False
        self._subscribed: bool = False
        self.started_at: float = 0.0
        self.estimator_task: Optional[Periodic] = None
        self.refresh_job_task: Optional[Periodic] = None
        self.refuse_new_miners: bool = False
        self.messages_timeout_task: Optional[Periodic] = None

        # For testing only.
        self._update_job_timestamp: bool = True

        self.last_submit_at: float = 0.0
        self.completed_jobs: int = 0
        self.txs_solved: int = 0
        self.blocks_found: int = 0
        self.supported_methods: Dict[str, Callable[[Any, JSONRPCId], None]] = {
            "subscribe": self.method_subscribe,
            "mining.subscribe": self.method_subscribe,
            "login": self.method_subscribe,
            "authorize": self.method_authorize,
            "mining.authorize": self.method_authorize,
            "submit": self.method_submit,
            "mining.submit": self.method_submit,
            "configure": self.method_configure,
            "mining.configure": self.method_configure,
            "multi_version": self.method_multi_version,
            "mining.multi_version": self.method_multi_version,
            "mining.extranonce.subscribe": self.method_extranonce_subscribe,
        }

        self.messages_in_transit: Dict[int, MessageInTransit] = {}
        self.next_message_id = 1

    @property
    def miner_type(self) -> str:
        """Return the type of the connected miner."""
        return self.miner_version.split("/")[0]

    def handle_result(self, result: Any, msgid: JSONRPCId) -> None:
        """Handle a result from JSONRPC."""
        if msgid not in self.messages_in_transit:
            self.log.warning(
                "Received result for unknown message", msgid=msgid, result=result
            )
            return

        assert type(msgid) is int

        message = self.messages_in_transit.pop(cast(int, msgid))

        if message.method == "client.get_version":
            self.log.info("Miner version: {}".format(result), miner_id=self.miner_id)
            self.miner_version = result
        else:
            self.log.error(
                "Cant handle result: {}".format(result),
                miner_id=self.miner_id,
                msgid=msgid,
            )

    def get_next_message_id(self) -> int:
        """Return the next message id."""
        msgid = self.next_message_id
        self.next_message_id += 1
        return msgid

    def send_and_track_request(self, method: str, params: Any) -> None:
        """Send a request to the client and track it."""
        msgid = self.get_next_message_id()

        self.messages_in_transit[msgid] = MessageInTransit(
            id=msgid,
            method=method,
            timeout=txstratum.time.time() + self.MESSAGE_TIMEOUT,
        )
        self.send_request(method, params, msgid)

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Set things up after a new connection is made.

        See: https://docs.python.org/3/library/asyncio-protocol.html#asyncio.BaseProtocol.connection_made
        """
        self.transport = cast(asyncio.Transport, transport)
        self.manager.add_connection(self)
        self.log = self.log.bind(miner_id=self.miner_id)
        self.log.debug("connection made")

    def connection_lost(self, exc: Optional[Exception]) -> None:
        """Clean up after a connection is lost.

        See: https://docs.python.org/3/library/asyncio-protocol.html#asyncio.BaseProtocol.connection_lost
        """
        self.log.debug("connection lost", exc=exc)
        if self._subscribed:
            self.log.info("Miner exited")
            self.manager.pubsub.emit(TxMiningEvents.PROTOCOL_MINER_DISCONNECTED, self)
        assert self.miner_id is not None
        self.manager.remove_connection(self)
        if self.estimator_task:
            asyncio.ensure_future(self.estimator_task.stop())
        if self.refresh_job_task:
            asyncio.ensure_future(self.refresh_job_task.stop())
        if self.messages_timeout_task:
            asyncio.ensure_future(self.messages_timeout_task.stop())

    def ask_miner_to_reconnect(self) -> None:
        """Ask the miner to reconnect. We just want to force it to disconnect with this."""
        self.log.info("Asking miner to reconnect")
        self.send_request("client.show_message", ["Will force reconnection"], None)
        self.send_request("client.reconnect", [], None)

        self.transport.close()

    def start_periodic_tasks(self) -> None:
        """Start periodic tasks."""
        if self.estimator_task is None:
            # Start estimator periodic task.
            self.estimator_task = Periodic(
                self.estimator_loop, self.ESTIMATOR_LOOP_INTERVAL
            )
            asyncio.ensure_future(self.estimator_task.start())

        if self.refresh_job_task is None:
            # Start refresh job periodic task.
            self.refresh_job_task = Periodic(self.refresh_job, self.JOB_UPDATE_INTERVAL)
            asyncio.ensure_future(self.refresh_job_task.start())

        if self.messages_timeout_task is None:
            # Start messages timeout periodic task.
            self.messages_timeout_task = Periodic(
                self.messages_timeout_job, self.MESSAGES_TIMEOUT_INTERVAL
            )
            asyncio.ensure_future(self.messages_timeout_task.start())

    async def messages_timeout_job(self) -> None:
        """Period task to check for messages that have timed out."""
        now = txstratum.time.time()

        should_delete = []

        for message in self.messages_in_transit.values():
            if now > message.timeout:
                should_delete.append(message.id)

        for msgid in should_delete:
            message = self.messages_in_transit.pop(msgid)
            self.log.warning("Message timed out", msg=message)

    def method_extranonce_subscribe(self, params: Any, msgid: JSONRPCId) -> None:
        """Handle extra-nonce request from JSONRPC."""
        self.send_result(msgid, True)

    def method_multi_version(self, params: Any, msgid: JSONRPCId) -> None:
        """Handle multi-version request from JSONRPC."""
        self.send_result(msgid, True)

    def method_subscribe(self, params: Any, msgid: JSONRPCId) -> None:
        """Handle subscribe request from JSONRPC."""
        if self.refuse_new_miners:
            self.send_error(msgid, self.SERVICE_SHUTTING_DOWN)
            self.log.info(
                "Refused miner subscription because we are shutting down",
                address=self.miner_address_str,
            )
            return

        if params and "address" in params and params["address"] is not None:
            try:
                address = params["address"]
                address_bytes = decode_address(address)
                if address_bytes[0] not in VALID_FIRST_BYTE:
                    self.log.warn("Forcing different network address")
                    address_bytes = bytearray(address_bytes)
                    address_bytes[0] = binary_to_int(settings.P2PKH_VERSION_BYTE)
                    address_bytes = bytes(address_bytes)
                self.miner_address = address_bytes
                self.miner_address_str = address
            except InvalidAddress:
                self.log.debug("Miner with invalid address", address=address)
                self.send_error(msgid, self.INVALID_ADDRESS)
                self.transport.close()
                return
        self._subscribed = True
        self.send_result("ok", msgid)
        self.log.info("Miner subscribed", address=self.miner_address_str)
        self.manager.pubsub.emit(TxMiningEvents.PROTOCOL_MINER_SUBSCRIBED, self)
        self.start_if_ready()

    def method_authorize(self, params: Any, msgid: JSONRPCId) -> None:
        """Handle authorize request from JSONRPC."""
        self._authorized = True
        self.log.info("Miner authorized")
        self.start_if_ready()

    def method_submit(self, params: Any, msgid: JSONRPCId) -> None:
        """Handle submit request from JSONRPC.

        Args:
            params: a dict containing `job_id` and `nonce`
        """
        self.log.debug("handle submit", msgid=msgid, params=params)
        if "job_id" not in params or "nonce" not in params:
            return self.send_error(
                msgid,
                self.INVALID_PARAMS,
                {"params": params, "required": ["job_id", "nonce"]},
            )

        try:
            job_id = bytes.fromhex(params["job_id"])
        except ValueError:
            return self.send_error(msgid, self.INVALID_PARAMS, "invalid job_id format")

        try:
            nonce = int(params["nonce"], 16)
        except ValueError:
            return self.send_error(msgid, self.INVALID_PARAMS, "invalid nonce")

        job = self.jobs.get(job_id, None)

        if job is None:
            # XXX Should we look into the job history? It may be necessary if the job was not marked as clean.
            return self.send_error(msgid, self.JOB_NOT_FOUND)

        if job.submitted_at is not None:
            self.log.debug("Job already submitted", job_id=job_id)
            return self.send_error(
                msgid,
                self.STALE_JOB,
                {
                    "current_job": self.current_job and self.current_job.uuid.hex(),
                    "job_id": job_id.hex(),
                },
            )

        obj = job.get_object()
        obj.nonce = nonce
        obj.update_hash()
        if not obj.verify_pow(override_weight=job.share_weight):
            self.log.error(
                "Invalid share weight",
                uuid=job_id.hex(),
                share_weight=job.share_weight,
                data=job.get_data().hex(),
            )
            return self.send_error(msgid, self.INVALID_SOLUTION)

        now = txstratum.time.time()
        self.completed_jobs += 1
        job.submitted_at = now
        self.last_submit_at = now
        self.send_result(msgid, "ok")

        self.manager.pubsub.emit(TxMiningEvents.PROTOCOL_JOB_COMPLETED, self)

        if isinstance(job, MinerBlockJob):
            assert job.started_at is not None
            dt = job.submitted_at - job.started_at
            self._submitted_work.append(
                SubmittedWork(job.submitted_at, job.share_weight, dt)
            )
        # Too many jobs too fast, increase difficulty out of caution (more than 10 submits within the last 10s)
        if sum(1 for x in self._submitted_work if now - x.timestamp < 10) > 10:
            # Doubles the difficulty
            self.set_current_weight(self.current_weight + 1)

        if obj.verify_pow():
            # We need to verify if the submited solution is valid, because we may
            # have sent to the miner a job with weight below the weight needed
            # to solve the block.
            self.manager.submit_solution(self, job, obj.get_struct_nonce())
            if obj.is_block:
                self.blocks_found += 1
            else:
                self.txs_solved += 1

        else:
            # If the solution is not valid, get a new job.
            self.manager.update_miner_job(self, clean=True)

    def method_configure(self, params: Any, msgid: JSONRPCId) -> None:
        """Handle stratum-extensions configuration from JSONRPC.

        See: https://github.com/slushpool/stratumprotocol/blob/master/stratum-extensions.mediawiki
        """
        self.log.debug("handle configure", msgid=msgid, params=params)
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
        now = txstratum.time.time()
        # remove old entries
        self._submitted_work = [
            x
            for x in self._submitted_work
            if now - x.timestamp < self.ESTIMATOR_WINDOW_INTERVAL
        ]
        # too little jobs, reduce difficulty
        if len(self._submitted_work) == 0:
            self.set_current_weight(self.current_weight - 0.5)
            return
        # otherwise, estimate the hashrate, and aim for a difficulty that approximates the target job time
        # window size
        if len(self._submitted_work) <= 1:
            return
        # delta and total logwork (considering the highest hash only)
        delta = 0.0
        logwork = 0.0
        for x in self._submitted_work:
            delta += x.dt
            logwork = sum_weights(logwork, x.weight)
        # calculate hashrate in TH/s
        self.hashrate_ghs = 2 ** (logwork - log2(delta) - 30)
        self.set_current_weight(logwork - log2(delta) + log2(self.TARGET_JOB_TIME))

    def set_current_weight(self, weight: float) -> None:
        """Set current weight and update miner's job."""
        self.current_weight = max(self.MIN_WEIGHT, weight)
        assert self.current_job is not None
        self.manager.update_miner_job(self, clean=False)

    def is_ready(self) -> bool:
        """Return True if miner is ready to mine."""
        if self._subscribed:
            return True
        return False

    def status(self) -> Dict[Any, Any]:
        """Return dict with useful metrics."""
        job_type = None
        if self.current_job is not None:
            job_type = "block" if self.current_job.is_block else "transaction"

        return {
            "id": self.miner_id,
            "version": self.miner_version,
            "hashrate_ghs": self.hashrate_ghs,
            "weight": self.current_weight,
            "miner_address": self.miner_address_str,
            "job_type": job_type,
            "started_at": self.started_at,
            "last_submit_at": self.last_submit_at,
            "uptime": self.uptime,
            "completed_jobs": self.completed_jobs,
            "txs_solved": self.txs_solved,
            "blocks_found": self.blocks_found,
        }

    @property
    def uptime(self) -> float:
        """Live uptime after the miner is ready."""
        if not self.started_at:
            return 0.0
        return txstratum.time.time() - self.started_at

    def start_if_ready(self) -> None:
        """Start mining if it is ready."""
        self.send_and_track_request("client.get_version", None)

        if not self.is_ready():
            return
        if self.current_job is not None:
            return
        self.started_at = txstratum.time.time()
        self.start_periodic_tasks()
        self.manager.mark_connection_as_ready(self)

    def is_mining_block(self) -> bool:
        """Return True if the miner is mining a block."""
        if self.current_job is None:
            return False
        return self.current_job.is_block

    async def refresh_job(self) -> None:
        """Refresh miner's job, updating timestamp.

        This is run as a periodic task.
        """
        assert self.current_job is not None

        self.manager.update_miner_job(self, clean=False)

    def update_job(self, job: "MinerJob", *, clean: bool) -> None:
        """Update miner's job. It is called by the manager.

        :param job: new job
        :param clean: if clean is True, the miner will be forced to abort the
            current job and immediately start mining the new job
        """
        # Set job's share weight
        job.share_weight = min(self.current_weight, job.get_weight())

        # Set miner address.
        if job.is_block and self.miner_address:
            assert isinstance(job, MinerBlockJob)
            job.set_mining_address(self.miner_address)

        # Update timestamp.
        if self._update_job_timestamp:
            job.update_timestamp()

        # Update when the job started mining
        job.started_at = txstratum.time.time()

        # Add job to the job list and set it as current job
        self.current_job = job
        self.jobs[job.uuid] = job

        # Send new job to the miner.
        job_data = {
            "data": job.get_header_without_nonce().hex(),
            "job_id": job.uuid.hex(),
            "nonce_size": job.get_nonce_size(),
            "weight": float(job.share_weight),
            "clean": clean,
        }
        self.log.debug("Update job", job=job_data, block=job.get_data().hex())
        self.send_request("job", job_data, msgid=None)

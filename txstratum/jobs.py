# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import enum
import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from hathorlib import BaseTransaction, Block
from hathorlib.scripts import create_output_script

import txstratum.time
from txstratum.utils import tx_or_block_from_bytes

if TYPE_CHECKING:
    from asyncio import TimerHandle


class JobStatus(enum.Enum):
    """Job status."""

    PENDING = "pending"
    GETTING_PARENTS = "getting-parents"
    ENQUEUED = "enqueued"
    MINING = "mining"
    DONE = "done"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"

    @classmethod
    def get_after_mining_states(cls) -> Set["JobStatus"]:
        """Return a set with all possible states after mining has finished."""
        return set([cls.DONE, cls.FAILED, cls.TIMEOUT, cls.CANCELLED])


class TxJob:
    """Tx job.

    It represents a tx job received by the API. It is wrapped by a MinerTxJob before
    reaching a miner.
    """

    def __init__(
        self,
        data: bytes,
        *,
        add_parents: bool = False,
        propagate: bool = False,
        timeout: Optional[float] = None
    ):
        """Init TxJob.

        add_parents: Add parents before mining tx.
        propagate: Propagate tx to the full node after it is mined.
        timeout: Mining timeout.
        """
        self._tx: BaseTransaction = tx_or_block_from_bytes(data)

        self.uuid: bytes = self._tx.get_funds_hash()
        self.add_parents: bool = add_parents
        self.propagate: bool = propagate
        self.timeout: Optional[float] = timeout

        self.expected_queue_time: float = 0
        self.expected_mining_time: float = 0

        self.status: JobStatus = JobStatus.PENDING
        self.message: str = ""
        self.created_at: float = txstratum.time.time()
        self.started_at: Optional[float] = None
        self.submitted_at: Optional[float] = None
        self.total_time: Optional[float] = None
        self.nonce: Optional[bytes] = None
        self.timestamp: Optional[int] = None

        self._timeout_timer: Optional[TimerHandle] = None
        self._cleanup_timer: Optional[TimerHandle] = None

    def is_failed(self) -> bool:
        """Return True if job has any status that indicates failure."""
        return self.status in [JobStatus.FAILED, JobStatus.TIMEOUT, JobStatus.CANCELLED]

    def is_done(self) -> bool:
        """Return True if job has any status that indicates success."""
        return self.status in [JobStatus.DONE]

    def get_tx(self) -> BaseTransaction:
        """Return the Transaction object of this job."""
        return self._tx

    def set_parents(self, parents: List[bytes]) -> None:
        """Set tx parents."""
        self._tx.parents = parents
        self._tx.update_hash()

    def mark_as_solved(self, *, nonce: bytes, timestamp: int) -> None:
        """Mark job as solved."""
        if self.status == JobStatus.DONE:
            # We received another solution. It may be either the same miner
            # submitting twice or two miners finding solutions. We can safely
            # skip the solution.
            return
        now = txstratum.time.time()
        self.status = JobStatus.DONE
        self.nonce = nonce
        self.timestamp = timestamp
        self.submitted_at = now
        self.total_time = now - self.created_at

    def mark_as_failed(self, message: str) -> None:
        """Mark job as failed."""
        self.status = JobStatus.FAILED
        self.message = message

    def get_header_without_nonce(self) -> bytes:
        """Return job's header without nonce."""
        return self._tx.get_header_without_nonce()

    def get_nonce_size(self) -> int:
        """Return job's nonce size."""
        return self._tx.SERIALIZATION_NONCE_SIZE

    def get_weight(self) -> float:
        """Return job's weight (difficulty)."""
        return self._tx.weight

    def get_mining_time(self) -> float:
        """Return the time it took to mine the job."""
        assert self.submitted_at is not None
        assert self.started_at is not None

        return self.submitted_at - self.started_at

    def get_waiting_time(self) -> float:
        """Return the time it took to start mining the job."""
        assert self.started_at is not None

        return self.started_at - self.created_at

    def to_dict(self) -> Dict[str, Any]:
        """Return a dict with an overview of the job.

        Returns:
            job_id: str, job identifier
            status: str, choices: pending, mining, done, failed, cancelled
            created_at: int, timestamp that the job was submitted
            expected_queue_time: int, expected time in queue (in seconds)
            expected_mining_time: int, expected time to be mined (in seconds)
            expected_total_time: int, sum of expected_queue_time and expected_mining_time (in seconds)
        """
        return {
            "job_id": self.uuid.hex(),
            "status": self.status.value,
            "message": self.message,
            "created_at": self.created_at,
            "tx": {
                "nonce": self.nonce.hex() if self.nonce else None,
                "parents": [x.hex() for x in self._tx.parents],
                "timestamp": self.timestamp,
                "weight": self._tx.weight,
            },
            "timeout": self.timeout,
            "submitted_at": self.submitted_at,
            "total_time": self.total_time,
            "expected_queue_time": self.expected_queue_time,
            "expected_mining_time": self.expected_mining_time,
            "expected_total_time": self.expected_queue_time + self.expected_mining_time,
        }


class MinerJob(ABC):
    """Base class for mining jobs."""

    uuid: bytes
    is_block: bool
    share_weight: float
    started_at: Optional[float]
    submitted_at: Optional[float]

    @abstractmethod
    def get_object(self) -> BaseTransaction:
        """Return the parsed object of this jobs."""
        raise NotImplementedError

    @abstractmethod
    def get_weight(self) -> float:
        """Return job's weight (difficulty)."""
        raise NotImplementedError

    @abstractmethod
    def get_header_without_nonce(self) -> bytes:
        """Return job's header without nonce."""
        raise NotImplementedError

    @abstractmethod
    def get_nonce_size(self) -> int:
        """Return job's nonce size."""
        raise NotImplementedError

    @abstractmethod
    def get_data(self) -> bytes:
        """Return data to be submitted to the backend."""
        raise NotImplementedError

    @abstractmethod
    def update_timestamp(self, *, force: bool = False) -> None:
        """Update job timestamp."""
        raise NotImplementedError


class MinerTxJob(MinerJob):
    """Tx mining job."""

    def __init__(self, tx_job: TxJob):
        """Init TxJob."""
        assert isinstance(tx_job, TxJob)
        self.tx_job: TxJob = tx_job

        # The tx_job could have been included in other MinerTxJob before, so we
        # don't want to overwrite it.
        if not tx_job.started_at:
            tx_job.started_at = txstratum.time.time()

        self._tx: BaseTransaction = tx_job.get_tx().clone()

        self.uuid: bytes = uuid.uuid4().bytes
        self.is_block: bool = False
        self.share_weight: float = 0
        self.created_at: float = txstratum.time.time()
        self.started_at: Optional[float] = None
        self.submitted_at: Optional[float] = None

    def get_object(self) -> BaseTransaction:
        """Return the parsed object of this jobs."""
        return self._tx

    def get_data(self) -> bytes:
        """Return data to be submitted to the backend."""
        return bytes(self._tx)

    def update_timestamp(self, *, force: bool = False) -> None:
        """Update job timestamp."""
        self._tx.timestamp = int(txstratum.time.time())

    def get_header_without_nonce(self) -> bytes:
        """Return job's header without nonce."""
        return self._tx.get_header_without_nonce()

    def get_nonce_size(self) -> int:
        """Return job's nonce size."""
        return self._tx.SERIALIZATION_NONCE_SIZE

    def get_weight(self) -> float:
        """Return job's weight (difficulty)."""
        return self._tx.weight


class MinerBlockJob(MinerJob):
    """Block mining job."""

    def __init__(self, data: bytes, height: int):
        """Init MinerBlockJob."""
        self._block: Block = Block.create_from_struct(data)
        self.height: int = height

        self.uuid: bytes = uuid.uuid4().bytes
        self.is_block: bool = True
        self.share_weight: float = 0
        self.created_at: float = txstratum.time.time()
        self.started_at: Optional[float] = None
        self.submitted_at: Optional[float] = None

    def get_data(self) -> bytes:
        """Return data to be submitted to the backend."""
        return bytes(self._block)

    def get_object(self) -> BaseTransaction:
        """Return the parsed object of this jobs."""
        return self._block

    def update_timestamp(self, *, force: bool = False) -> None:
        """Update job timestamp."""
        now = int(txstratum.time.time())
        if not force:
            delta = now - self._block.timestamp
            if delta < 0:
                return
            if delta > 30:
                # Skip if the new timestamp is too far away from the current timestamp.
                #
                # The timestamp is updated every template update. It means that we should only reach
                # this point if the template update fails or if the template's timestamp is too old.
                #
                # TODO Use the new mining api (`/v1a/mining_ws`) that includes min and max timestamp.
                return
        self._block.timestamp = now

    def get_header_without_nonce(self) -> bytes:
        """Return job's header without nonce."""
        return self._block.get_header_without_nonce()

    def get_nonce_size(self) -> int:
        """Return job's nonce size."""
        return self._block.SERIALIZATION_NONCE_SIZE

    def get_weight(self) -> float:
        """Return job's weight."""
        return self._block.weight

    def set_mining_address(self, address: bytes) -> None:
        """Set mining address."""
        assert len(self._block.outputs) == 1
        self._block.outputs[0].script = create_output_script(address)
        self._block.update_hash()

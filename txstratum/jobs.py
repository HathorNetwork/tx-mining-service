# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import enum
import hashlib
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set

from txstratum.commons import BaseTransaction, Block
from txstratum.commons.scripts import create_output_script
from txstratum.utils import tx_or_block_from_bytes


class JobStatus(enum.Enum):
    """Job status."""

    PENDING = 'pending'
    ENQUEUED = 'enqueued'
    GETTING_PARENTS = 'getting-parents'
    MINING = 'mining'
    DONE = 'done'
    FAILED = 'failed'
    TIMEOUT = 'timeout'
    CANCELLED = 'cancelled'

    @classmethod
    def get_after_mining_states(cls) -> Set['JobStatus']:
        """Return a set with all possible states after mining has finished."""
        return set([cls.DONE, cls.FAILED, cls.TIMEOUT, cls.CANCELLED])


class MinerJob(ABC):
    """Base class for jobs."""

    uuid: bytes
    is_block: bool
    share_weight: float
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
    def update_timestamp(self) -> None:
        """Update job timestamp."""
        raise NotImplementedError


class MinerTxJob(MinerJob):
    """Tx job."""

    def __init__(self, data: bytes, *, add_parents: bool = False, propagate: bool = False,
                 timeout: Optional[int] = None):
        """Init TxJob.

        add_parents: Add parents before mining tx.
        propagate: Propagate tx to the full node after it is mined.
        timeout: Mining timeout.
        """
        self._tx: BaseTransaction = tx_or_block_from_bytes(data)

        self.add_parents: bool = add_parents
        self.propagate: bool = propagate
        self.timeout: Optional[int] = timeout

        self.expected_queue_time: float = 0
        self.expected_mining_time: float = 0

        self.uuid: bytes = self.get_uuid(data)
        self.is_block: bool = False
        self.share_weight: float = 0
        self.status: JobStatus = JobStatus.PENDING
        self.message: str = ''
        self.created_at: float = time.time()
        self.submitted_at: Optional[float] = None
        self.total_time: Optional[float] = None
        self.nonce: Optional[bytes] = None

    @classmethod
    def get_uuid(cls, tx: bytes) -> bytes:
        """Return the job uuid."""
        s1 = hashlib.sha256(tx).digest()
        s2 = hashlib.sha256(s1).digest()
        return s2

    def get_object(self) -> BaseTransaction:
        """Return the parsed object of this jobs."""
        return self._tx

    def get_data(self) -> bytes:
        """Return data to be submitted to the backend."""
        return bytes(self._tx)

    def update_timestamp(self) -> None:
        """Update job timestamp."""
        self._tx.timestamp = int(time.time())

    def set_parents(self, parents: List[bytes]) -> None:
        """Set tx parents."""
        self._tx.parents = parents
        self._tx.update_hash()

    def mark_as_solved(self, nonce: bytes) -> None:
        """Mark job as solved."""
        now = time.time()
        self.status = JobStatus.DONE
        self.nonce = nonce
        self.submitted_at = now
        self.total_time = now - self.created_at

    def get_header_without_nonce(self) -> bytes:
        """Return job's header without nonce."""
        return self._tx.get_header_without_nonce()

    def get_nonce_size(self) -> int:
        """Return job's nonce size."""
        return self._tx.SERIALIZATION_NONCE_SIZE

    def get_weight(self) -> float:
        """Return job's weight (difficulty)."""
        return self._tx.weight

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
            'job_id': self.uuid.hex(),
            'status': self.status.value,
            'message': self.message,
            'created_at': self.created_at,
            'tx': {
                'nonce': self.nonce.hex() if self.nonce else None,
                'parents': [x.hex() for x in self._tx.parents],
                'timestamp': self._tx.timestamp,
                'weight': self._tx.weight,
            },
            'submitted_at': self.submitted_at,
            'total_time': self.total_time,
            'expected_queue_time': self.expected_queue_time,
            'expected_mining_time': self.expected_mining_time,
            'expected_total_time': self.expected_queue_time + self.expected_mining_time,
        }


class MinerBlockJob(MinerJob):
    """Block job."""

    def __init__(self, data: bytes, height: int):
        """Init MinerBlockJob."""
        self._block: Block = Block.create_from_struct(data)
        self.height: int = height

        self.uuid: bytes = self.get_uuid(data)
        self.is_block: bool = True
        self.share_weight: float = 0
        self.created_at: float = time.time()
        self.submitted_at: Optional[float] = None

    def get_data(self) -> bytes:
        """Return data to be submitted to the backend."""
        return bytes(self._block)

    def get_object(self) -> BaseTransaction:
        """Return the parsed object of this jobs."""
        return self._block

    def update_timestamp(self) -> None:
        """Update job timestamp."""
        self._block.timestamp = int(time.time())

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

    @classmethod
    def get_uuid(cls, data: bytes) -> bytes:
        """Return uuid for the data."""
        s1 = hashlib.sha256(data).digest()
        s2 = hashlib.sha256(s1).digest()
        return s2

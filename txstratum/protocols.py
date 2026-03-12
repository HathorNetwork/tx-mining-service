# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""Structural typing protocols shared across modules."""

from typing import Any, Dict, Optional, Protocol

from txstratum.jobs import TxJob


class MiningManager(Protocol):
    """Structural interface shared by TxMiningManager and DevMiningManager.

    Both managers implement this interface, which is consumed by App (HTTP API)
    and HealthCheck. Using a Protocol instead of a concrete base class allows
    the two managers to remain independent (no shared inheritance).
    """

    tx_jobs: Dict[bytes, TxJob]

    def status(self) -> Dict[Any, Any]:
        """Return status dict."""
        ...

    def add_job(self, job: TxJob) -> bool:
        """Add a new transaction to be mined."""
        ...

    def get_job(self, uuid: bytes) -> Optional[TxJob]:
        """Return the TxJob for the given uuid."""
        ...

    def cancel_job(self, job: TxJob) -> None:
        """Cancel a mining job."""
        ...

    def has_any_miner(self) -> bool:
        """Return whether any miner is active."""
        ...

    def has_any_submitted_job_in_period(self, period: int) -> bool:
        """Return whether any job was submitted in the given period."""
        ...

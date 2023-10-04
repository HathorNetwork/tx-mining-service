from abc import ABC, abstractmethod
import asyncio
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from hathorlib.client import HathorClient

if TYPE_CHECKING:
    from txstratum.manager import TxMiningManager


class ComponentType(str, Enum):
    """Enum used to store the component types that can be used in the HealthCheckComponentStatus class."""

    DATASTORE = "datastore"
    INTERNAL = "internal"
    FULLNODE = "fullnode"


class HealthCheckStatus(str, Enum):
    """Enum used to store the component status that can be used in the HealthCheckComponentStatus class."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class ComponentHealthCheck:
    """This class is used to store the result of a health check in a specific component."""

    component_name: str
    component_type: ComponentType
    status: HealthCheckStatus
    output: str
    time: Optional[str] = None
    component_id: Optional[str] = None
    observed_value: Optional[str] = None
    observed_unit: Optional[str] = None

    def update(self, new_values: Dict[str, Any]) -> None:
        """
        Update the object with the new values passed as kwargs.

        Also updates the time field with the current time with the format YYYY-MM-DDTHH:mm:ssZ
        """
        self.time = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        for key, value in new_values.items():
            setattr(self, key, value)

    def to_json(self) -> Dict[str, str]:
        """Return a dict representation of the object. All field names are converted to camel case."""
        json = {
            "componentType": self.component_type.value,
            "status": self.status.value,
            "output": self.output,
        }

        if self.time:
            json["time"] = self.time

        if self.component_id:
            json["componentId"] = self.component_id

        if self.observed_value:
            assert (
                self.observed_unit is not None
            ), "observed_unit must be set if observed_value is set"

            json["observedValue"] = self.observed_value
            json["observedUnit"] = self.observed_unit

        return json


@dataclass
class HealthCheckResult:
    """This class is used to store the result of a health check in the tx-mining-service."""

    status: HealthCheckStatus
    description: str
    checks: Dict[str, List[ComponentHealthCheck]]

    def __post_init__(self) -> None:
        """Perform some validations after the object is initialized."""
        # Make sure the checks dict is not empty
        if not self.checks:
            raise ValueError("checks dict cannot be empty")

        # Make sure the status is valid
        if self.status not in HealthCheckStatus:
            raise ValueError("Invalid status")

    def get_http_status_code(self) -> int:
        """Return the HTTP status code for the status."""
        if self.status in [HealthCheckStatus.PASS]:
            return 200
        elif self.status in [HealthCheckStatus.WARN, HealthCheckStatus.FAIL]:
            return 503
        else:
            raise ValueError(f"Missing treatment for status {self.status}")

    def to_json(self) -> Dict[str, Any]:
        """Return a dict representation of the object. All field names are converted to camel case."""
        return {
            "status": self.status.value,
            "description": self.description,
            "checks": {k: [c.to_json() for c in v] for k, v in self.checks.items()},
        }


class ComponentHealthCheckInterface(ABC):
    """This is an interface to be used by other classes implementing health checks for components."""

    @abstractmethod
    async def get_health_check(self) -> ComponentHealthCheck:
        """Return the health check status for the component."""
        raise NotImplementedError()


class HealthCheck:
    """This is the main class that will use the other classes to check the health of the components.

    It will aggregate the responses into a final object to be returned following our standards.
    """

    def __init__(self, manager: "TxMiningManager", backend: "HathorClient") -> None:
        """Init the class with the components that will be checked."""
        self.health_check_components: List[ComponentHealthCheckInterface] = [
            MiningHealthCheck(manager),
            FullnodeHealthCheck(backend),
        ]

    async def get_health_check(self) -> HealthCheckResult:
        """Return the health check status for the tx-mining-service."""
        components_health_checks = await asyncio.gather(*[c.get_health_check() for c in self.health_check_components])
        components_status = {c.status for c in components_health_checks}

        overall_status = HealthCheckStatus.PASS
        if HealthCheckStatus.FAIL in components_status:
            overall_status = HealthCheckStatus.FAIL
        elif HealthCheckStatus.WARN in components_status:
            overall_status = HealthCheckStatus.WARN

        return HealthCheckResult(
            status=overall_status,
            description="health of tx-mining-service",
            checks={c.component_name: [c] for c in components_health_checks},
        )


class FullnodeHealthCheck(ComponentHealthCheckInterface):
    """This class will check the health of the fullnode by sending a request to its /v1a/health."""

    def __init__(self, backend: "HathorClient") -> None:
        """Init the class with the fullnode backend."""
        self.backend = backend

    async def get_health_check(self) -> ComponentHealthCheck:
        """Return the fullnode health check status."""
        health_check = ComponentHealthCheck(
            component_name="fullnode",
            component_type=ComponentType.FULLNODE,
            # TODO: Ideally we should not use private fields. We'll fix this when fixing line 170
            component_id=self.backend._base_url,
            status=HealthCheckStatus.PASS,
            time=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            output="fullnode is responding correctly",
        )

        try:
            # TODO: We need to get the health information from the fullnode, but it's not implemented yet
            await self.backend.version()

            return health_check
        except Exception:
            health_check.status = HealthCheckStatus.FAIL
            health_check.output = "couldn't connect to fullnode"

            return health_check


class MiningHealthCheck(ComponentHealthCheckInterface):
    """
    This class receives a manager instance as parameter and implements a health check method for it.

    It will:
    - Check that the manager has at least 1 miner
    - Check that all 'tx_jobs' in the manager in the last 5 minutes have a status of done
      and have a total_time lesser than 10 seconds.
    - Check that all miners have submitted at least 1 job in the last 1 hour.
      Note that subimitting a job doesn't mean solving a tx.

    If at least one of the 'tx_jobs' has a 'total_time' of more than 10 seconds, the health check
    will be returned as 'warn'.

    If some of the 'tx_jobs' has status different than 'done', the health check will be returned
    as 'fail'.

    If there are no miners, the health check will be returned as 'fail'

    If there are miners, but any of them has submitted a job in the last 1 hour, the health check
    will be returned as 'fail'
    """

    def __init__(self, manager: "TxMiningManager") -> None:
        """Init the class with the manager instance."""
        self.manager = manager
        self.last_manager_status = ComponentHealthCheck(
            component_name="manager",
            component_type=ComponentType.INTERNAL,
            status=HealthCheckStatus.PASS,
            output="everything is ok",
        )

    async def get_health_check(self) -> ComponentHealthCheck:
        """Return the manager health check status."""
        # Check that the manager has at least 1 miner
        if not self.manager.has_any_miner():
            return ComponentHealthCheck(
                component_name="manager",
                component_type=ComponentType.INTERNAL,
                status=HealthCheckStatus.FAIL,
                output="no miners connected",
            )

        # Check that all the miners submitted in the last 1 hour. If not, return failed.
        if not self.manager.has_any_submitted_job_in_period(period=3600):
            return ComponentHealthCheck(
                component_name="manager",
                component_type=ComponentType.INTERNAL,
                status=HealthCheckStatus.FAIL,
                output="no miners submitted a job in the last 1 hour",
            )

        if not self.manager.tx_jobs:
            # We just return the last saved status in case there are no jobs in the last 5 minutes
            return self.last_manager_status

        for job in self.manager.tx_jobs.values():
            if job.is_failed():
                self.last_manager_status.update(
                    {
                        "status": HealthCheckStatus.FAIL,
                        "output": "some tx_jobs in the last 5 minutes have failed",
                    }
                )

                return self.last_manager_status
            if job.is_done():
                assert (
                    job.total_time is not None
                ), "total_time must be set if job is done"

                if job.total_time > 10:
                    self.last_manager_status.update(
                        {
                            "status": HealthCheckStatus.WARN,
                            "output": "some tx_jobs in the last 5 minutes took more than 10 seconds to be solved",
                        }
                    )

                    return self.last_manager_status

        self.last_manager_status.update(
            {"status": HealthCheckStatus.PASS, "output": "everything is ok"}
        )

        return self.last_manager_status

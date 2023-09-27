from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from txstratum.manager import TxMiningManager


class ComponentType(str, Enum):
    """
    Enum used to store the component types that can be used in the HealthCheckComponentStatus class.
    """
    DATASTORE = 'datastore'
    INTERNAL_COMPONENT = 'internal_component'


class HealthCheckStatus(str, Enum):
    """
    Enum used to store the component status that can be used in the HealthCheckComponentStatus class.
    """
    PASS = 'pass'
    WARN = 'warn'
    FAIL = 'fail'


@dataclass
class ComponentHealthCheck:
    """
    This class is used to store the result of a health check in a specific component.
    """
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
        self.time = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

        for key, value in new_values.items():
            setattr(self, key, value)

    def to_json(self) -> dict:
        """
        Return a dict representation of the object. All field names are converted to camel case.
        """
        json = {
            'componentType': self.component_type.value,
            'status': self.status.value,
            'output': self.output
        }

        if self.component_id:
            json['componentId'] = self.component_id

        if self.observed_value:
            assert self.observed_unit is not None, 'observed_unit must be set if observed_value is set'

            json['observedValue'] = self.observed_value
            json['observedUnit'] = self.observed_unit

        return json


@dataclass
class HealthCheckResult:
    status: HealthCheckStatus
    description: str
    checks: Dict[str, List[ComponentHealthCheck]]

    def get_http_status_code(self) -> int:
        """
        Return the HTTP status code for the status.
        """
        if self.status == HealthCheckStatus.PASS:
            return 200
        elif self.status == HealthCheckStatus.WARN:
            return 503
        elif self.status == HealthCheckStatus.FAIL:
            return 503
        else:
            raise ValueError('Invalid status')

    def to_json(self) -> dict:
        """
        Return a dict representation of the object. All field names are converted to camel case.
        """
        return {
            'status': self.status.value,
            'description': self.description,
            'checks': {k: [c.to_json() for c in v] for k, v in self.checks.items()}
        }


class HealthCheckInterface(ABC):
    """
    This is an interface to be used by other classes implementing health checks for components.
    """
    @abstractmethod
    def get_health_check(self) -> ComponentHealthCheck:
        """
        Return the health check status for the component.
        """
        raise NotImplementedError()


class HealthCheck(HealthCheckInterface):
    """This is the main class that will use the other classes to check the health of the components.
    It will aggregate the responses into a final object to be returned following our standards.
    """
    def __init__(self, manager: "TxMiningManager") -> None:
        self.health_check_components: List[HealthCheckInterface] = [
            ManagerHealthCheck(manager)
        ]

    def get_health_check(self) -> HealthCheckResult:
        components_health_checks = [c.get_health_check() for c in self.health_check_components]
        components_status = {c.status for c in components_health_checks}

        overall_status = HealthCheckStatus.PASS
        if HealthCheckStatus.FAIL in components_status:
            overall_status = HealthCheckStatus.FAIL
        elif HealthCheckStatus.WARN in components_status:
            overall_status = HealthCheckStatus.WARN

        return HealthCheckResult(
            status=overall_status,
            description='health of txstratum service',
            checks={c.component_name: [c] for c in components_health_checks}
        )


# class FullnodeHealthCheck(HealthCheckInterface):
#     """
#     This class will check the health of the fullnode by sending a request to its /v1a/health
#     """


class ManagerHealthCheck(HealthCheckInterface):
    """
    This class receives a manager instance as parameter and implements a health check method for it that will:
    - Check that the manager has at least 1 miner
    - Check that all 'tx_jobs' in the manager in the last 5 minutes have a status of done
      and have a total_time lesser than 10 seconds.

    If at least one of the 'tx_jobs' has a 'total_time' of more than 10 seconds, the health check
    will be returned as 'warn'.

    If some of the 'tx_jobs' has status different than 'done', the health check will be returned
    as 'fail'.

    If there are no miners, the health check will be returned as 'fail'
    """
    def __init__(self, manager: "TxMiningManager") -> None:
        self.manager = manager
        self.last_manager_status = ComponentHealthCheck(
            component_name='manager',
            component_type=ComponentType.INTERNAL_COMPONENT,
            status=HealthCheckStatus.PASS,
            output='everything is ok'
        )

    def get_health_check(self) -> ComponentHealthCheck:
        """
        Return the manager health check status.
        """
        if not self.manager.has_any_miner():
            self.last_manager_status.update({
                'status': HealthCheckStatus.FAIL,
                'output': 'no miners connected'
            })

            return self.last_manager_status

        if not self.manager.tx_jobs:
            # We just return the last status in case there are no jobs in the last 5 minutes
            return self.last_manager_status

        for job in self.manager.tx_jobs.values():
            if job.is_failed():
                self.last_manager_status.update({
                    'status': HealthCheckStatus.FAIL,
                    'output': 'some tx_jobs in the last 5 minutes have failed'
                })

                return self.last_manager_status
            if job.total_time > 10:
                self.last_manager_status.update({
                    'status': HealthCheckStatus.WARN,
                    'output': 'some tx_jobs in the last 5 minutes took more than 10 seconds to be solved'
                })

                return self.last_manager_status

        self.last_manager_status.update({
            'status': HealthCheckStatus.PASS,
            'output': 'everything is ok'
        })

        return self.last_manager_status
from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING

from hathorlib.client import HathorClient
from healthcheck import (
    Healthcheck,
    HealthcheckCallbackResponse,
    HealthcheckHTTPComponent,
    HealthcheckInternalComponent,
    HealthcheckResponse,
    HealthcheckStatus,
)

if TYPE_CHECKING:
    from txstratum.manager import TxMiningManager


class HealthCheck:
    """This is the main class that will use the other classes to check the health of the components.

    It will aggregate the responses into a final object to be returned following our standards.
    """

    def __init__(self, manager: "TxMiningManager", backend: "HathorClient") -> None:
        """Init the class with the components that will be checked."""
        self.healthcheck = Healthcheck("TxMiningService")

        mining_healthcheck = MiningHealthCheck(manager)
        mining_healthcheck_component = HealthcheckInternalComponent(
            mining_healthcheck.COMPONENT_NAME
        )
        mining_healthcheck_component.add_healthcheck(
            mining_healthcheck.get_health_check
        )

        fullnode_healthcheck = FullnodeHealthCheck(backend)
        fullnode_healthcheck_component = HealthcheckHTTPComponent(
            name=fullnode_healthcheck.COMPONENT_NAME,
            id=backend._base_url,
        )
        fullnode_healthcheck_component.add_healthcheck(
            fullnode_healthcheck.get_health_check
        )

        self.healthcheck.add_component(mining_healthcheck_component)
        self.healthcheck.add_component(fullnode_healthcheck_component)

    async def get_health_check(self) -> HealthcheckResponse:
        """Return the health check status for the tx-mining-service."""
        return await self.healthcheck.run()


class ComponentHealthCheckInterface(ABC):
    """This is an interface to be used by other classes implementing health checks for components."""

    @abstractmethod
    async def get_health_check(self) -> HealthcheckCallbackResponse:
        """Return the health check status for the component."""
        raise NotImplementedError()


class FullnodeHealthCheck(ComponentHealthCheckInterface):
    """This class will check the health of the fullnode by sending a request to its /v1a/health."""

    COMPONENT_NAME = "fullnode"

    def __init__(self, backend: "HathorClient") -> None:
        """Init the class with the fullnode backend."""
        self.backend = backend

    async def get_health_check(self) -> HealthcheckCallbackResponse:
        """Return the fullnode health check status."""
        response = HealthcheckCallbackResponse(
            status=HealthcheckStatus.PASS,
            output="Fullnode is responding correctly",
        )

        try:
            # TODO: We need to get the health information from the fullnode, but it's not implemented yet
            await self.backend.version()

            return response
        except Exception as e:
            response.status = HealthcheckStatus.FAIL
            response.output = f"Couldn't connect to fullnode: {str(e)}"

            return response


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

    If any of the 'tx_jobs' has status different than 'done', the health check will be returned
    as 'fail'.

    If there are no miners, the health check will be returned as 'fail'

    If there are miners, but none of them has submitted a job in the last 1 hour, the health check
    will be returned as 'fail'
    """

    NO_JOBS_SUBMITTED_THRESHOLD = 3600  # 1 hour
    JOB_MINING_TIME_THRESHOLD = 10  # 10 seconds
    COMPONENT_NAME = "manager"

    def __init__(self, manager: "TxMiningManager") -> None:
        """Init the class with the manager instance."""
        self.manager = manager
        self.last_response = HealthcheckCallbackResponse(
            status=HealthcheckStatus.PASS,
            output="everything is ok",
        )
        self.last_response_update_time = datetime.utcnow().strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    def _update_last_response(self, status: HealthcheckStatus, output: str) -> None:
        """Update the last_response object with the new values."""
        self.last_response.status = status
        self.last_response.output = output
        self.last_response_update_time = datetime.utcnow().strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    async def get_health_check(self) -> HealthcheckCallbackResponse:
        """Return the manager health check status."""
        # Check that the manager has at least 1 miner
        if not self.manager.has_any_miner():
            return HealthcheckCallbackResponse(
                status=HealthcheckStatus.FAIL,
                output="No miners connected",
            )

        # Check that all the miners submitted in the last 1 hour. If not, return failed.
        if not self.manager.has_any_submitted_job_in_period(
            period=self.NO_JOBS_SUBMITTED_THRESHOLD
        ):
            return HealthcheckCallbackResponse(
                status=HealthcheckStatus.FAIL,
                output="No miners submitted a job in the last 1 hour",
            )

        if not self.manager.tx_jobs:
            return HealthcheckCallbackResponse(
                status=self.last_response.status,
                output=(
                    "We had no tx_jobs in the last 5 minutes, so we are just returning the last observed"
                    f" status from {self.last_response_update_time}. The output was: {self.last_response.output}"
                ),
            )

        failed_jobs = []
        long_running_jobs = []

        for job in self.manager.tx_jobs.values():
            if job.is_failed():
                failed_jobs.append(job)
            if job.is_done():
                assert (
                    job.total_time is not None
                ), "total_time must be set if job is done"

                if job.total_time > self.JOB_MINING_TIME_THRESHOLD:
                    long_running_jobs.append(job)

        if failed_jobs or long_running_jobs:
            self._update_last_response(
                status=HealthcheckStatus.FAIL
                if failed_jobs
                else HealthcheckStatus.WARN,
                output=(
                    f"We had {len(failed_jobs)} failed jobs and {len(long_running_jobs)}"
                    " long running jobs in the last 5 minutes"
                ),
            )

            return self.last_response

        self._update_last_response(
            status=HealthcheckStatus.PASS, output="Everything is ok"
        )

        return self.last_response

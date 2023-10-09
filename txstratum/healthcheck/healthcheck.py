import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, List

from hathorlib.client import HathorClient

from txstratum.healthcheck.models import (
    ComponentHealthCheck,
    ComponentHealthCheckInterface,
    ComponentType,
    HealthCheckResult,
    HealthCheckStatus,
)

if TYPE_CHECKING:
    from txstratum.manager import TxMiningManager


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
        components_health_checks = await asyncio.gather(
            *[c.get_health_check() for c in self.health_check_components]
        )
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

    COMPONENT_NAME = "fullnode"

    def __init__(self, backend: "HathorClient") -> None:
        """Init the class with the fullnode backend."""
        self.backend = backend

    async def get_health_check(self) -> ComponentHealthCheck:
        """Return the fullnode health check status."""
        health_check = ComponentHealthCheck(
            component_name=self.COMPONENT_NAME,
            component_type=ComponentType.FULLNODE,
            # TODO: Ideally we should not use private fields. We'll fix this when fixing line 170
            component_id=self.backend._base_url,
            status=HealthCheckStatus.PASS,
            time=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            output="Fullnode is responding correctly",
        )

        try:
            # TODO: We need to get the health information from the fullnode, but it's not implemented yet
            await self.backend.version()

            return health_check
        except Exception as e:
            health_check.status = HealthCheckStatus.FAIL
            health_check.output = f"Couldn't connect to fullnode: {str(e)}"

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

    NO_JOBS_SUBMITTED_THRESHOLD = 3600  # 1 hour
    JOB_MINING_TIME_THRESHOLD = 10  # 10 seconds
    COMPONENT_NAME = "manager"

    def __init__(self, manager: "TxMiningManager") -> None:
        """Init the class with the manager instance."""
        self.manager = manager
        self.last_manager_status = ComponentHealthCheck(
            component_name=self.COMPONENT_NAME,
            component_type=ComponentType.INTERNAL,
            status=HealthCheckStatus.PASS,
            output="everything is ok",
        )

    async def get_health_check(self) -> ComponentHealthCheck:
        """Return the manager health check status."""
        # Check that the manager has at least 1 miner
        if not self.manager.has_any_miner():
            return ComponentHealthCheck(
                component_name=self.COMPONENT_NAME,
                component_type=ComponentType.INTERNAL,
                status=HealthCheckStatus.FAIL,
                output="No miners connected",
            )

        # Check that all the miners submitted in the last 1 hour. If not, return failed.
        if not self.manager.has_any_submitted_job_in_period(
            period=self.NO_JOBS_SUBMITTED_THRESHOLD
        ):
            return ComponentHealthCheck(
                component_name=self.COMPONENT_NAME,
                component_type=ComponentType.INTERNAL,
                status=HealthCheckStatus.FAIL,
                output="No miners submitted a job in the last 1 hour",
            )

        if not self.manager.tx_jobs:
            return ComponentHealthCheck(
                component_name=self.COMPONENT_NAME,
                component_type=ComponentType.INTERNAL,
                status=self.last_manager_status.status,
                output=(
                    "We had no tx_jobs in the last 5 minutes, so we are just returning the last observed"
                    f" status from {self.last_manager_status.time}. The output was: {self.last_manager_status.output}"
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
            self.last_manager_status.update(
                status=HealthCheckStatus.FAIL
                if failed_jobs
                else HealthCheckStatus.WARN,
                output=(
                    f"We had {len(failed_jobs)} failed jobs and {len(long_running_jobs)}"
                    " long running jobs in the last 5 minutes"
                ),
            )

            return self.last_manager_status

        self.last_manager_status.update(
            status=HealthCheckStatus.PASS, output="Everything is ok"
        )

        return self.last_manager_status

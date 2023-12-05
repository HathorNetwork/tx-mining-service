from unittest.mock import MagicMock

import asynctest  # type: ignore[import]
from healthcheck import HealthcheckStatus

from txstratum.healthcheck.healthcheck import (
    FullnodeHealthCheck,
    HealthCheck,
    MiningHealthCheck,
)


class HathorClientMock:
    _base_url = "http://localhost:8080"

    async def version(self):
        return {"version": "1.0.0"}


class TestFullnodeHealthCheck(asynctest.TestCase):  # type: ignore[misc]
    def setUp(self) -> None:
        self.mock_hathor_client = MagicMock()
        self.fullnode_health_check = FullnodeHealthCheck(
            backend=self.mock_hathor_client
        )

    async def test_get_health_check_with_a_healthy_fullnode(self):
        """Test the response we should generated for a healthy fullnode"""
        # Mock the implementation of the hathor_client.version.
        async def side_effect():
            return {"version": "1.0.0"}

        self.mock_hathor_client.version.side_effect = side_effect
        self.mock_hathor_client._base_url = "http://localhost:8080"

        result = await self.fullnode_health_check.get_health_check()
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.output, "Fullnode is responding correctly")

    async def test_get_health_check_with_an_unhealthy_fullnode(self):
        """Test the response we should generated for an unhealthy fullnode"""
        self.mock_hathor_client.version.side_effect = Exception("error")
        self.mock_hathor_client._base_url = "http://localhost:8080"

        result = await self.fullnode_health_check.get_health_check()
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.output, "Couldn't connect to fullnode: error")


class TestMiningHealthCheck(asynctest.TestCase):  # type: ignore[misc]
    def setUp(self):
        self.manager = MagicMock()
        self.mining_health_check = MiningHealthCheck(manager=self.manager)

    async def test_get_health_check_no_miners(self):
        # Preparation
        self.manager.has_any_miner.return_value = False
        # Execution
        result = await self.mining_health_check.get_health_check()
        # Assertion
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.output, "No miners connected")

    async def test_get_health_check_no_submitted_job_in_period(self):
        # Preparation
        self.manager.has_any_miner.return_value = True
        self.manager.has_any_submitted_job_in_period.return_value = False
        # Execution
        result = await self.mining_health_check.get_health_check()
        # Assertion
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.output, "No miners submitted a job in the last 1 hour")

    async def test_get_health_check_failed_job(self):
        # Preparation
        self.manager.has_any_miner.return_value = True
        self.manager.has_any_submitted_job_in_period.return_value = True
        job = MagicMock()
        job.is_failed.return_value = True
        job.total_time = 9
        self.manager.tx_jobs = {"job_id": job}
        # Execution
        result = await self.mining_health_check.get_health_check()
        # Assertion
        self.assertEqual(result.status, "fail")
        self.assertEqual(
            result.output,
            "We had 1 failed jobs and 0 long running jobs in the last 5 minutes",
        )

    async def test_get_health_check_slow_job(self):
        # Preparation
        self.manager.has_any_miner.return_value = True
        self.manager.has_any_submitted_job_in_period.return_value = True
        job = MagicMock()
        job.is_failed.return_value = False
        job.total_time = 11
        self.manager.tx_jobs = {"job_id": job}
        # Execution
        result = await self.mining_health_check.get_health_check()
        # Assertion
        self.assertEqual(result.status, "warn")
        self.assertEqual(
            result.output,
            "We had 0 failed jobs and 1 long running jobs in the last 5 minutes",
        )

    async def test_get_health_check_ok(self):
        # Preparation
        self.manager.has_any_miner.return_value = True
        self.manager.has_any_submitted_job_in_period.return_value = True
        job = MagicMock()
        job.is_failed.return_value = False
        job.total_time = 9
        self.manager.tx_jobs = {"job_id": job}
        # Execution
        result = await self.mining_health_check.get_health_check()
        # Assertion
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.output, "Everything is ok")

    async def test_return_last_status(self):
        """
        This tests the case where we have no tx_jobs in the last 5 minutes, but we had a previous status of failure.

        We should return the previous status and include its output in the new output.
        """
        # Preparation
        self.manager.has_any_miner.return_value = True
        self.manager.has_any_submitted_job_in_period.return_value = True
        self.manager.tx_jobs = {}
        self.mining_health_check._update_last_response(
            status=HealthcheckStatus.FAIL,
            output="We had 1 failed jobs and 0 long running jobs in the last 5 minutes",
        )
        # Execution
        result = await self.mining_health_check.get_health_check()
        # Assertion
        self.assertEqual(result.status, "fail")
        self.assertEqual(
            0,
            result.output.index(
                "We had no tx_jobs in the last 5 minutes, so we are just returning the last observed status from"
            ),
        )


class TestHealthCheck(asynctest.TestCase):  # type: ignore[misc]
    def setUp(self):

        self.mock_hathor_client = HathorClientMock()
        self.mock_manager = MagicMock()

        self.health_check = HealthCheck(
            manager=self.mock_manager, backend=self.mock_hathor_client
        )

    async def test_get_health_check_success(self):
        """Tests the response we should generate when everything is ok"""
        # Mock the implementation of the hathor_client.version.
        async def side_effect():
            return {"version": "1.0.0"}

        self.mock_hathor_client.version = MagicMock()
        self.mock_hathor_client.version.side_effect = side_effect

        self.mock_hathor_client._base_url = "http://localhost:8080"

        self.mock_manager.has_any_miner.return_value = True
        self.mock_manager.has_any_submitted_job_in_period.return_value = True
        job = MagicMock()
        job.is_failed.return_value = False
        job.total_time = 9
        self.mock_manager.tx_jobs = {"job_id": job}

        result = await self.health_check.get_health_check()
        self.assertEqual(result.checks["manager"][0].status, HealthcheckStatus.PASS)
        self.assertEqual(result.checks["manager"][0].output, "Everything is ok")
        self.assertEqual(result.checks["manager"][0].component_name, "manager")
        self.assertEqual(result.checks["manager"][0].component_type, "internal")
        self.assertEqual(result.checks["fullnode"][0].status, HealthcheckStatus.PASS)
        self.assertEqual(
            result.checks["fullnode"][0].component_id, "http://localhost:8080"
        )
        self.assertEqual(result.checks["fullnode"][0].component_name, "fullnode")
        self.assertEqual(result.checks["fullnode"][0].component_type, "http")
        self.assertEqual(
            result.checks["fullnode"][0].output, "Fullnode is responding correctly"
        )
        self.assertEqual(result.status, HealthcheckStatus.PASS)

    async def test_get_health_check_fullnode_failure(self):
        """Tests the response we should generate when the fullnode is unhealthy"""
        self.mock_hathor_client.version = MagicMock()
        self.mock_hathor_client.version.side_effect = Exception("error")
        self.mock_hathor_client._base_url = "http://localhost:8080"

        self.mock_manager.has_any_miner.return_value = True
        self.mock_manager.has_any_submitted_job_in_period.return_value = True
        job = MagicMock()
        job.is_failed.return_value = False
        job.total_time = 9
        self.mock_manager.tx_jobs = {"job_id": job}

        result = await self.health_check.get_health_check()
        self.assertEqual(result.checks["manager"][0].status, HealthcheckStatus.PASS)
        self.assertEqual(result.checks["manager"][0].output, "Everything is ok")
        self.assertEqual(result.checks["fullnode"][0].status, HealthcheckStatus.FAIL)
        self.assertEqual(
            result.checks["fullnode"][0].output, "Couldn't connect to fullnode: error"
        )
        self.assertEqual(result.status, HealthcheckStatus.FAIL)

    async def test_get_health_check_mining_failure(self):
        """Tests the response we should generate when the mining is unhealthy"""
        # Mock the implementation of the hathor_client.version.
        async def side_effect():
            return {"version": "1.0.0"}

        self.mock_hathor_client.version = MagicMock()
        self.mock_hathor_client.version.side_effect = side_effect
        self.mock_hathor_client._base_url = "http://localhost:8080"

        self.mock_manager.has_any_miner.return_value = True
        self.mock_manager.has_any_submitted_job_in_period.return_value = False

        result = await self.health_check.get_health_check()
        self.assertEqual(result.checks["manager"][0].status, HealthcheckStatus.FAIL)
        self.assertEqual(
            result.checks["manager"][0].output,
            "No miners submitted a job in the last 1 hour",
        )
        self.assertEqual(result.checks["fullnode"][0].status, HealthcheckStatus.PASS)
        self.assertEqual(
            result.checks["fullnode"][0].output, "Fullnode is responding correctly"
        )
        self.assertEqual(result.status, HealthcheckStatus.FAIL)

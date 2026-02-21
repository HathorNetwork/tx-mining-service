"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from hathorlib.base_transaction import tx_or_block_from_bytes

import txstratum.time
from txstratum.api import App
from txstratum.dev.block_miner import solve_block
from txstratum.dev.manager import DevMiningManager
from txstratum.dev.tx_miner import solve_tx
from txstratum.jobs import JobStatus

# Same TX1_DATA from test_api.py
TX1_DATA = bytes.fromhex(
    "0001000102000000000000089c0d40a9b1edfb499bc624833fde87ae459d495000393f4aaa00006"
    "a473045022100c407d5e8f411f9ae582ebd7acbfcb6ea6170332709fb69acaa34c1b426f1d8f502"
    "2003847963768eca9bcdf46e758319fb2699fd28ab657d00f54bef46c37a90405e2103755f2920f"
    "f7dc32dc5414cea1cf9e078347f40894caf0c03637d083dbb261c5c000003e800001976a914a04c"
    "9e2a0291f53c618fdad2ecb37748efb0eeeb88ac0000151800001976a914545f1156a3b00df622b"
    "1d92968c21b962e9d7aa588ac4032a8228c4020c35ed18547020000000047c9881d2bf348d5ffd6"
    "ce8398d6bc5d17b3bea75a53c15b7480be950000006ed5794bf69ebe7d7d75e7a0024d98acb85cb"
    "9c101b59b8b6073e8667c84e2ee77"
)


def update_timestamp(tx_bytes: bytes, *, delta: int = 0) -> bytes:
    """Update timestamp to current timestamp."""
    tx = tx_or_block_from_bytes(tx_bytes)
    tx.timestamp = int(txstratum.time.time()) + delta
    return bytes(tx)


class TestSolveTx(AioHTTPTestCase):
    """Test the synchronous tx miner."""

    __test__ = True

    async def get_application(self):
        backend = MagicMock()
        backend.get_tx_parents = AsyncMock(return_value=[b"\x00" * 32, b"\x01" * 32])
        self.manager = DevMiningManager(backend=backend)
        self.healthcheck = MagicMock()
        self.myapp = App(self.manager, self.healthcheck)
        return self.myapp.app

    def test_solve_tx_trivial_weight(self):
        """Test that solve_tx works with a transaction of trivial weight."""
        tx_bytes = update_timestamp(TX1_DATA)
        tx = tx_or_block_from_bytes(tx_bytes)
        # Set weight to 1 (trivial - ~50% of nonces work)
        tx.weight = 1.0
        result = solve_tx(tx)
        self.assertTrue(result)
        self.assertTrue(tx.verify_pow())

    def test_solve_tx_standard_weight(self):
        """Test that solve_tx works with the transaction's default weight."""
        tx_bytes = update_timestamp(TX1_DATA)
        tx = tx_or_block_from_bytes(tx_bytes)
        result = solve_tx(tx)
        self.assertTrue(result)
        self.assertTrue(tx.verify_pow())


class TestDevMiningManager(AioHTTPTestCase):
    """Test the DevMiningManager."""

    __test__ = True

    async def get_application(self):
        self.backend = MagicMock()
        self.backend.get_tx_parents = AsyncMock(
            return_value=[b"\x00" * 32, b"\x01" * 32]
        )
        self.backend.push_tx_or_block = AsyncMock(return_value=True)
        self.manager = DevMiningManager(backend=self.backend)
        await self.manager.start()
        self.healthcheck = MagicMock()
        self.myapp = App(self.manager, self.healthcheck)
        return self.myapp.app

    @unittest_run_loop
    async def test_submit_and_poll_job(self):
        """Test submitting a job and polling for its status."""
        tx_hex = update_timestamp(TX1_DATA).hex()
        resp = await self.client.request(
            "POST", "/submit-job", json={"tx": tx_hex}
        )
        self.assertEqual(200, resp.status)
        data = await resp.json()
        job_id = data["job_id"]
        self.assertIn("status", data)
        self.assertEqual(0, data["expected_total_time"])

        # Wait for async mining to complete (TX1_DATA has weight ~32, may need more time)
        for _ in range(50):
            await asyncio.sleep(0.1)
            resp = await self.client.request(
                "GET", "/job-status", params={"job-id": job_id}
            )
            data = await resp.json()
            if data["status"] == "done":
                break

        self.assertEqual(200, resp.status)
        self.assertEqual("done", data["status"])
        self.assertIsNotNone(data["tx"]["nonce"])

    @unittest_run_loop
    async def test_submit_job_with_parents(self):
        """Test submitting a job that needs parents."""
        tx_hex = update_timestamp(TX1_DATA).hex()
        resp = await self.client.request(
            "POST",
            "/submit-job",
            json={"tx": tx_hex, "add_parents": True},
        )
        self.assertEqual(200, resp.status)
        data = await resp.json()
        job_id = data["job_id"]

        # Wait for async mining to complete
        for _ in range(50):
            await asyncio.sleep(0.1)
            resp = await self.client.request(
                "GET", "/job-status", params={"job-id": job_id}
            )
            data = await resp.json()
            if data["status"] == "done":
                break

        self.assertEqual("done", data["status"])
        # Verify parents were set
        self.assertEqual(2, len(data["tx"]["parents"]))

    @unittest_run_loop
    async def test_submit_job_with_propagate(self):
        """Test submitting a job with propagate=True."""
        tx_hex = update_timestamp(TX1_DATA).hex()
        resp = await self.client.request(
            "POST",
            "/submit-job",
            json={"tx": tx_hex, "propagate": True},
        )
        self.assertEqual(200, resp.status)
        data = await resp.json()
        job_id = data["job_id"]

        # Wait for async mining to complete
        for _ in range(50):
            await asyncio.sleep(0.1)
            resp = await self.client.request(
                "GET", "/job-status", params={"job-id": job_id}
            )
            data = await resp.json()
            if data["status"] == "done":
                break

        # Verify push_tx_or_block was called
        self.backend.push_tx_or_block.assert_called_once()

    @unittest_run_loop
    async def test_health_check(self):
        """Test health endpoint works with dev manager."""
        health_check_result = MagicMock()
        health_check_result.get_http_status_code.return_value = 200
        health_check_result.to_json.return_value = {"status": "pass"}

        async def side_effect():
            return health_check_result

        self.healthcheck.get_health_check.side_effect = side_effect

        resp = await self.client.request("GET", "/health")
        self.assertEqual(200, resp.status)
        data = await resp.json()
        self.assertEqual({"status": "pass"}, data)

    @unittest_run_loop
    async def test_mining_status(self):
        """Test mining-status endpoint with dev manager."""
        resp = await self.client.request("GET", "/mining-status")
        self.assertEqual(200, resp.status)
        data = await resp.json()
        self.assertTrue(data["dev_miner"])
        self.assertEqual(0, len(data["miners"]))

    @unittest_run_loop
    async def test_manager_status(self):
        """Test DevMiningManager status method."""
        status = self.manager.status()
        self.assertTrue(status["dev_miner"])
        self.assertTrue(self.manager.has_any_miner())
        self.assertTrue(self.manager.has_any_submitted_job_in_period(3600))

    @unittest_run_loop
    async def test_duplicate_job_submission(self):
        """Test that submitting the same job twice returns existing job."""
        tx_hex = update_timestamp(TX1_DATA).hex()

        resp1 = await self.client.request(
            "POST", "/submit-job", json={"tx": tx_hex}
        )
        data1 = await resp1.json()
        self.assertEqual(200, resp1.status)

        resp2 = await self.client.request(
            "POST", "/submit-job", json={"tx": tx_hex}
        )
        data2 = await resp2.json()
        self.assertEqual(200, resp2.status)

        self.assertEqual(data1["job_id"], data2["job_id"])


BLOCK_DATA = bytes.fromhex(
    "000001ffffffe8b789180000001976a9147fd4ae0e4fb2d2854e76d359029d8078bb9"
    "9649e88ac40350000000000005e0f84a9000000000000000000000000000000278a7e"
)


class TestSolveBlock(AioHTTPTestCase):
    """Test the block mining solve function."""

    __test__ = True

    async def get_application(self):
        from aiohttp import web

        return web.Application()

    def test_solve_block_from_template(self):
        """Test that solve_block can solve a block from a real template."""
        from hathorlib import Block

        block = Block.create_from_struct(BLOCK_DATA)
        result = solve_block(block)
        self.assertTrue(result)
        self.assertTrue(block.verify_pow())

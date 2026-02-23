"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""

"""Tests for the dev-miner mode.

These tests validate the three core components of the dev-miner:

1. solve_tx / solve_block — brute-force PoW solvers that replace stratum miners
2. DevMiningManager — drop-in replacement for TxMiningManager, tested through
   the same HTTP API endpoints that production uses (submit-job, job-status, etc.)
3. BlockMiner — background block producer, tested for interval regularity under
   both trivial and slow mining conditions
"""

import asyncio
import time as _time
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from hathorlib.base_transaction import tx_or_block_from_bytes

import txstratum.time
from txstratum.api import App
from txstratum.dev.block_miner import BlockMiner, solve_block
from txstratum.dev.manager import DevMiningManager
from txstratum.dev.tx_miner import solve_tx
from txstratum.jobs import JobStatus

# Same TX1_DATA from test_api.py — a serialized transaction that we use as a
# realistic input for PoW solving. The weight is ~32, so without
# --test-mode-tx-weight it requires ~2^32 nonce iterations (a few seconds).
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
    """Update timestamp to current timestamp.

    The fullnode rejects transactions with timestamps too far from the current
    time, so we refresh the timestamp before each test.
    """
    tx = tx_or_block_from_bytes(tx_bytes)
    tx.timestamp = int(txstratum.time.time()) + delta
    return bytes(tx)


# ---------------------------------------------------------------------------
# Transaction PoW tests
# ---------------------------------------------------------------------------


class TestSolveTx(AioHTTPTestCase):
    """Test the synchronous tx miner (solve_tx).

    These verify that the nonce brute-force loop finds a valid PoW solution
    for both trivial weight (~1, simulating --test-mode-tx-weight) and the
    transaction's natural weight (~32).
    """

    __test__ = True

    async def get_application(self):
        backend = MagicMock()
        backend.get_tx_parents = AsyncMock(return_value=[b"\x00" * 32, b"\x01" * 32])
        self.manager = DevMiningManager(backend=backend)
        self.healthcheck = MagicMock()
        self.myapp = App(self.manager, self.healthcheck)
        return self.myapp.app

    def test_solve_tx_trivial_weight(self):
        """With weight=1 (--test-mode-tx-weight), ~50% of nonces are valid."""
        tx_bytes = update_timestamp(TX1_DATA)
        tx = tx_or_block_from_bytes(tx_bytes)
        tx.weight = 1.0
        result = solve_tx(tx)
        self.assertTrue(result)
        self.assertTrue(tx.verify_pow())

    def test_solve_tx_standard_weight(self):
        """With the transaction's natural weight (~32), solve_tx still works
        but takes more iterations. This validates the full nonce range.
        """
        tx_bytes = update_timestamp(TX1_DATA)
        tx = tx_or_block_from_bytes(tx_bytes)
        result = solve_tx(tx)
        self.assertTrue(result)
        self.assertTrue(tx.verify_pow())


# ---------------------------------------------------------------------------
# DevMiningManager tests (via HTTP API)
# ---------------------------------------------------------------------------


class TestDevMiningManager(AioHTTPTestCase):
    """Test DevMiningManager through the same HTTP API used in production.

    These tests exercise the full job lifecycle: submit-job → poll job-status →
    done. They prove that DevMiningManager is a valid drop-in for TxMiningManager
    without any API-level changes.
    """

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
        """Basic lifecycle: submit a tx, poll until solved."""
        tx_hex = update_timestamp(TX1_DATA).hex()
        resp = await self.client.request(
            "POST", "/submit-job", json={"tx": tx_hex}
        )
        self.assertEqual(200, resp.status)
        data = await resp.json()
        job_id = data["job_id"]
        self.assertIn("status", data)
        # expected_total_time is 0 because there's no queue or stratum latency.
        self.assertEqual(0, data["expected_total_time"])

        # Poll for completion. Mining runs in an async task (via run_in_executor),
        # so we need to give it time to finish.
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
        """Test that add_parents=True fetches parents from the fullnode before mining."""
        tx_hex = update_timestamp(TX1_DATA).hex()
        resp = await self.client.request(
            "POST",
            "/submit-job",
            json={"tx": tx_hex, "add_parents": True},
        )
        self.assertEqual(200, resp.status)
        data = await resp.json()
        job_id = data["job_id"]

        for _ in range(50):
            await asyncio.sleep(0.1)
            resp = await self.client.request(
                "GET", "/job-status", params={"job-id": job_id}
            )
            data = await resp.json()
            if data["status"] == "done":
                break

        self.assertEqual("done", data["status"])
        self.assertEqual(2, len(data["tx"]["parents"]))

    @unittest_run_loop
    async def test_submit_job_with_propagate(self):
        """Test that propagate=True pushes the solved tx to the fullnode."""
        tx_hex = update_timestamp(TX1_DATA).hex()
        resp = await self.client.request(
            "POST",
            "/submit-job",
            json={"tx": tx_hex, "propagate": True},
        )
        self.assertEqual(200, resp.status)
        data = await resp.json()
        job_id = data["job_id"]

        for _ in range(50):
            await asyncio.sleep(0.1)
            resp = await self.client.request(
                "GET", "/job-status", params={"job-id": job_id}
            )
            data = await resp.json()
            if data["status"] == "done":
                break

        self.backend.push_tx_or_block.assert_called_once()

    @unittest_run_loop
    async def test_health_check(self):
        """Health endpoint works unchanged with DevMiningManager."""
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
        """mining-status endpoint exposes the dev_miner flag.

        Clients can use this flag to detect that the service is running in
        dev-miner mode (e.g., to adjust timeout expectations).
        """
        resp = await self.client.request("GET", "/mining-status")
        self.assertEqual(200, resp.status)
        data = await resp.json()
        self.assertTrue(data["dev_miner"])
        # No stratum miners in dev-miner mode.
        self.assertEqual(0, len(data["miners"]))

    @unittest_run_loop
    async def test_manager_status(self):
        """DevMiningManager.status() and health-related methods."""
        status = self.manager.status()
        self.assertTrue(status["dev_miner"])
        # has_any_miner() always returns True — the dev-miner itself is the miner.
        self.assertTrue(self.manager.has_any_miner())
        self.assertTrue(self.manager.has_any_submitted_job_in_period(3600))

    @unittest_run_loop
    async def test_duplicate_job_submission(self):
        """Submitting the same tx twice returns the existing job (same as production)."""
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


# ---------------------------------------------------------------------------
# Block mining tests
# ---------------------------------------------------------------------------

# A serialized block template used for testing solve_block.
BLOCK_DATA = bytes.fromhex(
    "000001ffffffe8b789180000001976a9147fd4ae0e4fb2d2854e76d359029d8078bb9"
    "9649e88ac40350000000000005e0f84a9000000000000000000000000000000278a7e"
)


class TestSolveBlock(AioHTTPTestCase):
    """Test the block PoW solver (solve_block)."""

    __test__ = True

    async def get_application(self):
        from aiohttp import web

        return web.Application()

    def test_solve_block_from_template(self):
        """Verify that solve_block can find a valid nonce for a real block template."""
        from hathorlib import Block

        block = Block.create_from_struct(BLOCK_DATA)
        result = solve_block(block)
        self.assertTrue(result)
        self.assertTrue(block.verify_pow())


class TestBlockMinerInterval(AioHTTPTestCase):
    """Test that BlockMiner produces blocks at a steady cadence.

    This is the key property that makes the dev-miner useful for integration
    tests: blocks arrive at predictable intervals, so tests can reason about
    when reward locks release and confirmations accumulate.

    Two scenarios are tested:
    1. Trivial PoW (--test-mode-block-weight): solve_block is instant, so the
       interval is entirely determined by the sleep.
    2. Slow PoW (no --test-mode-block-weight): solve_block takes a significant
       fraction of the interval. The timing compensation in BlockMiner._run()
       (sleep for interval minus elapsed time) should keep block-to-block time
       close to the configured interval.
    """

    __test__ = True

    async def get_application(self):
        from aiohttp import web

        return web.Application()

    def _make_miner(self, block_interval_ms, timestamps):
        """Create a BlockMiner with a mocked backend that records push timestamps."""
        backend = MagicMock()
        template = MagicMock()
        template.data = BLOCK_DATA
        template.height = 1
        backend.get_block_template = AsyncMock(return_value=template)

        async def record_push(*args, **kwargs):
            timestamps.append(_time.monotonic())

        backend.push_tx_or_block = AsyncMock(side_effect=record_push)

        miner = BlockMiner(backend=backend, block_interval_ms=block_interval_ms)
        return miner

    async def _wait_for_blocks(self, timestamps, num_blocks, timeout_s=5.0):
        """Poll until enough block timestamps are recorded or timeout."""
        deadline = _time.monotonic() + timeout_s
        while len(timestamps) < num_blocks and _time.monotonic() < deadline:
            await asyncio.sleep(0.02)

    def _assert_regular_intervals(self, timestamps, num_blocks, block_interval_ms,
                                  tolerance_pct=0.40):
        """Assert that block-to-block intervals are within tolerance of the target."""
        self.assertGreaterEqual(
            len(timestamps),
            num_blocks,
            f"Expected at least {num_blocks} blocks but only got {len(timestamps)}",
        )

        intervals_ms = [
            (timestamps[i + 1] - timestamps[i]) * 1000
            for i in range(num_blocks - 1)
        ]
        tolerance_ms = block_interval_ms * tolerance_pct

        for i, interval in enumerate(intervals_ms):
            self.assertAlmostEqual(
                interval,
                block_interval_ms,
                delta=tolerance_ms,
                msg=(
                    f"Interval block {i + 1} -> {i + 2}: {interval:.0f}ms "
                    f"(expected ~{block_interval_ms}ms +/-{tolerance_ms:.0f}ms)"
                ),
            )

    @unittest_run_loop
    async def test_blocks_mined_at_regular_intervals_trivial_weight(self):
        """With trivial block weight (--test-mode-block-weight), solve_block
        returns almost instantly, so the interval is purely sleep-driven.
        """
        block_interval_ms = 200
        num_blocks = 5
        timestamps = []

        miner = self._make_miner(block_interval_ms, timestamps)

        # Mock solve_block to return immediately (simulates weight ~1).
        with patch(
            "txstratum.dev.block_miner.solve_block", return_value=True
        ):
            await miner.start()
            await self._wait_for_blocks(timestamps, num_blocks)
            await miner.stop()

        self._assert_regular_intervals(timestamps, num_blocks, block_interval_ms)

    @unittest_run_loop
    async def test_blocks_mined_at_regular_intervals_slow_mining(self):
        """Even when solve_block takes 60-90% of the interval (simulating
        environments without --test-mode-block-weight), the timing compensation
        in _run() keeps block-to-block time close to the configured interval.

        This proves the `remaining = interval - elapsed` logic works correctly.
        """
        block_interval_ms = 200
        num_blocks = 5
        timestamps = []
        call_count = 0

        # Simulate mining that takes 60-90% of the interval.
        mining_delays_s = [0.15, 0.18, 0.12, 0.16, 0.14, 0.17]

        def slow_solve_block(block):
            nonlocal call_count
            delay = mining_delays_s[min(call_count, len(mining_delays_s) - 1)]
            call_count += 1
            _time.sleep(delay)
            return True

        miner = self._make_miner(block_interval_ms, timestamps)

        with patch(
            "txstratum.dev.block_miner.solve_block", side_effect=slow_solve_block
        ):
            await miner.start()
            await self._wait_for_blocks(timestamps, num_blocks)
            await miner.stop()

        self._assert_regular_intervals(timestamps, num_blocks, block_interval_ms)

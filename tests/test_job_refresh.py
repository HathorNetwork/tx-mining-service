"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""

import asyncio
import random
import unittest
from typing import Any, Dict, List, cast
from unittest.mock import MagicMock, Mock

import txstratum.time
from tests.test_manager import TX1_DATA, TX2_DATA, HathorClientTest
from txstratum.jobs import MinerBlockJob, MinerTxJob, TxJob
from txstratum.manager import TxMiningManager
from txstratum.protocol import StratumProtocol, SubmittedWork
from txstratum.utils import Ticker

BLOCK_DATA_1 = bytes.fromhex(
    "000001ffffffe8b789180000001976a9147fd4ae0e4fb2d2854e76d359029d8078bb9"
    "9649e88ac40350000000000005e0f84a9000000000000000000000000000000278a7e"
)


class FakeTimeMixin:
    now: float = 1_700_000_000.0

    def enable_fake_time(self) -> None:
        txstratum.time.set_time_function(lambda: self.now)

    def disable_fake_time(self) -> None:
        txstratum.time.set_time_function(None)


class RefreshJobTestCase(FakeTimeMixin, unittest.TestCase):
    def setUp(self):
        self.enable_fake_time()
        self.now = 1_700_000_000.01
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.client = HathorClientTest(server_url="")
        self.manager = TxMiningManager(
            backend=self.client,
            pubsub=MagicMock(),
            address="HC7w4j7mPet49BBN5a2An3XUiPvK6C1TL7",
        )
        self.loop.run_until_complete(self.manager.start())
        self.loop.run_until_complete(self.manager.wait_for_block_template())

    def tearDown(self):
        self.loop.run_until_complete(self.manager.stop())
        self.loop.close()
        self.disable_fake_time()

    def _ready_miner(self) -> StratumProtocol:
        conn = StratumProtocol(self.manager)
        conn.connection_made(transport=Mock())
        conn.method_subscribe(params={}, msgid=None)
        conn.method_authorize(params=None, msgid=None)
        conn.send_request = MagicMock(return_value=None)  # type: ignore[method-assign]
        return conn

    def _jobs_sent(self, conn: StratumProtocol) -> List[Dict[str, Any]]:
        mock = cast(MagicMock, conn.send_request)
        return [c[1][1] for c in mock.mock_calls if c[1][0] == "job"]

    def _refresh(self, conn: StratumProtocol) -> None:
        self.loop.run_until_complete(conn.refresh_job())

    def test_refresh_without_job_asks_for_one(self):
        # A miner can be ready before the first block template. The refresh
        # must ask for a job instead of failing (the failure stopped the loop).
        manager = MagicMock()
        conn = StratumProtocol(manager)
        conn.connection_made(transport=Mock())
        self.assertIsNone(conn.current_job)
        self._refresh(conn)
        manager.update_miner_job.assert_called_once_with(conn)

    def test_refresh_tx_job_once_per_second(self):
        conn = self._ready_miner()
        self.manager.add_job(TxJob(TX1_DATA))
        tx_job = conn.current_job
        ts0 = tx_job.get_object().timestamp
        self.assertEqual(ts0, int(self.now))
        conn.send_request.reset_mock()

        # Same second: the header would not change, so nothing is sent.
        self.now += 0.5
        self._refresh(conn)
        self.assertEqual([], self._jobs_sent(conn))

        # Next second: a new header with the new timestamp, same tx, clean=False.
        self.now += 0.51
        self._refresh(conn)
        jobs = self._jobs_sent(conn)
        self.assertEqual(1, len(jobs))
        self.assertFalse(jobs[0]["clean"])
        self.assertIs(conn.current_job.tx_job, tx_job.tx_job)
        self.assertEqual(ts0 + 1, conn.current_job.get_object().timestamp)
        self.assertNotEqual(
            tx_job.get_mining_header_without_nonce().hex(), jobs[0]["data"]
        )

    def test_refresh_tx_job_when_queue_changes(self):
        conn = self._ready_miner()
        job1 = TxJob(TX1_DATA)
        self.manager.add_job(job1)
        self.manager.add_job(TxJob(TX2_DATA))
        # The first tx leaves the queue in the same second: the miner must get the next one.
        self.manager.tx_queue.remove(job1)
        conn.send_request.reset_mock()
        self._refresh(conn)
        jobs = self._jobs_sent(conn)
        self.assertEqual(1, len(jobs))
        self.assertIsNot(conn.current_job.tx_job, job1)

    def test_refresh_block_job_every_two_seconds(self):
        conn = self._ready_miner()
        self.assertTrue(conn.current_job.is_block)
        conn.send_request.reset_mock()

        self.now += 1
        self._refresh(conn)
        self.assertEqual([], self._jobs_sent(conn))

        self.now += 1
        self._refresh(conn)
        self.assertEqual(1, len(self._jobs_sent(conn)))


class BlockWorkTimeTestCase(FakeTimeMixin, unittest.TestCase):
    def setUp(self):
        self.enable_fake_time()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.manager = MagicMock()
        self.manager.tx_queue = []
        self.protocol = StratumProtocol(self.manager)
        self.protocol.connection_made(MagicMock())

    def tearDown(self):
        self.loop.close()
        self.disable_fake_time()

    def _set_block_job(self) -> None:
        self.protocol.update_job(MinerBlockJob(BLOCK_DATA_1, 0), clean=False)

    def test_refresh_does_not_reset_block_time(self):
        self._set_block_job()
        for _ in range(10):
            self.now += 2
            self._set_block_job()
        self.now += 1
        self.assertAlmostEqual(21.0, self.protocol._take_block_work_time(self.now))
        # The count starts again after a share.
        self.now += 3
        self.assertAlmostEqual(3.0, self.protocol._take_block_work_time(self.now))

    def test_time_on_tx_jobs_does_not_count(self):
        self._set_block_job()
        self.now += 100
        self.protocol.update_job(MinerTxJob(TxJob(TX1_DATA)), clean=True)
        self.now += 50
        self._set_block_job()
        self.now += 10
        self.assertAlmostEqual(110.0, self.protocol._take_block_work_time(self.now))

    def test_estimator_with_refreshes(self):
        """A miner with a known hashrate, refreshed every second, is estimated right."""
        rng = random.Random(1426)
        hashrate_ghs = 6.0
        weight = 33.0
        share_rate = hashrate_ghs * 2**30 / 2**weight  # shares per second
        self._set_block_job()
        start = self.now
        next_share = start + rng.expovariate(share_rate)
        end = start + 800
        while self.now < end:
            tick = self.now + 1
            while next_share < tick:
                self.now = next_share
                dt = self.protocol._take_block_work_time(self.now)
                self.protocol._submitted_work.append(
                    SubmittedWork(self.now, weight, dt)
                )
                next_share += rng.expovariate(share_rate)
            self.now = tick
            self._set_block_job()
        self.loop.run_until_complete(self.protocol.estimator_loop())
        self.assertAlmostEqual(
            hashrate_ghs, self.protocol.hashrate_ghs, delta=0.15 * hashrate_ghs
        )


class TickerTestCase(FakeTimeMixin, unittest.TestCase):
    def tearDown(self):
        self.disable_fake_time()

    def test_next_delay(self):
        self.enable_fake_time()
        ticker = Ticker(MagicMock())
        self.now = 1000.25
        self.assertAlmostEqual(0.76, ticker.next_delay())
        self.now = 1000.999
        self.assertAlmostEqual(0.011, ticker.next_delay())

    def test_keeps_running_after_an_exception(self):
        loop = asyncio.new_event_loop()
        calls = []

        async def afunc():
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("first call fails")

        async def run():
            ticker = Ticker(afunc)
            ticker.next_delay = lambda: 0.001  # type: ignore[method-assign]
            await ticker.start()
            while len(calls) < 3:
                await asyncio.sleep(0.001)
            await ticker.stop()

        loop.run_until_complete(asyncio.wait_for(run(), 5))
        loop.close()
        self.assertGreaterEqual(len(calls), 3)

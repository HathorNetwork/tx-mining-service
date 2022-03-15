"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""
import asyncio
import time
import unittest
from unittest.mock import MagicMock

import numpy.random  # type: ignore

from txstratum.jobs import MinerBlockJob
from txstratum.protocol import StratumProtocol, SubmittedWork

BLOCK_DATA_1 = bytes.fromhex(
    "000001ffffffe8b789180000001976a9147fd4ae0e4fb2d2854e76d359029d8078bb9"
    "9649e88ac40350000000000005e0f84a9000000000000000000000000000000278a7e"
)


class DifficultyTestCase(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        manager = MagicMock()
        transport = MagicMock()
        self.protocol = StratumProtocol(manager)
        self.protocol.connection_made(transport)
        self.protocol.current_job = MinerBlockJob(BLOCK_DATA_1, 0)

    def test_estimator_no_job(self):
        self.protocol.current_job = None
        self.loop.run_until_complete(self.protocol.estimator_loop())

    def test_estimator_empty(self):
        self.loop.run_until_complete(self.protocol.estimator_loop())

    def test_estimator_one_work(self):
        now = time.time()
        self.protocol._submitted_work.append(SubmittedWork(now, 25.0, 8))
        self.loop.run_until_complete(self.protocol.estimator_loop())

    def test_estimator_fixed_weight(self):
        now = time.time()
        hashpower = 2**40
        for _ in range(200):
            weight = self.protocol.current_weight
            geometric_p = 2 ** (-weight)
            trials = numpy.random.geometric(geometric_p)
            dt = 1.0 * trials / hashpower
            self.protocol._submitted_work.append(SubmittedWork(now + dt, weight, dt))
            self.loop.run_until_complete(self.protocol.estimator_loop())
            print("!! hashrate_ghs:", self.protocol.hashrate_ghs)
            now += dt

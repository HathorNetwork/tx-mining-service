"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""
import time

import asynctest  # type: ignore

import txstratum.time


class TimeTestCase(asynctest.ClockedTestCase):  # type: ignore
    async def test_system_clock_time(self):
        t1 = time.time()
        t2 = txstratum.time.time()
        diff = abs(t1 - t2)
        self.assertLess(diff, 0.1)

    async def test_clock_time(self):
        from tests.utils import Clock

        clock = Clock(self.loop)
        ref = clock.ref_time

        try:
            clock.enable()
            self.assertLess(abs(txstratum.time.time() - ref), 0.1)
            ref += 100
            await self.advance(100)
            self.assertLess(abs(txstratum.time.time() - ref), 0.1)
        finally:
            clock.disable()

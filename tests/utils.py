"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""

import asyncio
import unittest
from asyncio.events import AbstractEventLoop
from typing import Optional

import txstratum.time


class Clock:
    def __init__(self, loop: Optional[AbstractEventLoop]):
        if loop is None:
            loop = asyncio.get_event_loop()
        self.loop = loop
        self.ref_time = txstratum.time.time()
        # If inside a ClockedTestCase, record the current synthetic offset
        # so that time() is derived purely from advance() calls (no real-time drift).
        self._initial_offset: Optional[float] = getattr(
            loop, "_test_clock_offset", None
        )

    def time(self) -> float:
        current_offset = getattr(self.loop, "_test_clock_offset", None)
        if current_offset is not None and self._initial_offset is not None:
            return float(self.ref_time + (current_offset - self._initial_offset))
        # Fallback for non-test usage
        return float(self.ref_time + self.loop.time())

    def enable(self) -> None:
        txstratum.time.set_time_function(self.time)

    def disable(self) -> None:
        txstratum.time.set_time_function(None)


class ClockedTestCase(unittest.IsolatedAsyncioTestCase):
    """Replacement for asynctest.ClockedTestCase compatible with Python 3.11+.

    Provides a controllable fake clock for testing time-dependent async code.
    After ``asyncSetUp`` runs, ``self.loop`` is the running event loop and its
    ``time()`` method is patched so that ``await self.advance(seconds)`` jumps
    the loop clock forward while still allowing the event loop to run normally.
    """

    loop: asyncio.AbstractEventLoop

    async def asyncSetUp(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.loop._test_clock_offset = 0.0  # type: ignore[attr-defined]
        _real_time = self.loop.time
        loop_ref = self.loop

        def _fake_time() -> float:
            return float(_real_time() + loop_ref._test_clock_offset)  # type: ignore[attr-defined]

        # Shadow the loop's time() on the instance so internal scheduling uses it.
        self.loop.time = _fake_time  # type: ignore[method-assign]

    async def advance(self, seconds: float) -> None:
        """Advance the fake clock by ``seconds`` and drain all due callbacks."""
        self.loop._test_clock_offset += seconds  # type: ignore[attr-defined]
        # Yield to the event loop repeatedly so that any asyncio.sleep() or
        # call_later() callbacks whose scheduled time has now passed get executed.
        for _ in range(1000):
            await asyncio.sleep(0)
            ready = bool(getattr(self.loop, "_ready", ()))
            scheduled = getattr(self.loop, "_scheduled", ())
            if not ready and (not scheduled or scheduled[0]._when > self.loop.time()):
                break
        else:
            raise AssertionError("advance() did not drain due callbacks")

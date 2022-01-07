"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""

import asyncio
from asyncio.events import AbstractEventLoop
from typing import Optional

import txstratum.time


class Clock:
    def __init__(self, loop: Optional[AbstractEventLoop]):
        if loop is None:
            loop = asyncio.get_event_loop()
        self.loop = loop
        self.ref_time = txstratum.time.time()

    def time(self) -> float:
        return self.ref_time + self.loop.time()

    def enable(self) -> None:
        txstratum.time.set_time_function(self.time)

    def disable(self) -> None:
        txstratum.time.set_time_function(None)


# XXX: We could use unittest's IsolatedAsyncioTestCase instead, but it's available only in Python 3.8+.
def async_test(coro):
    def wrapper(*args, **kwargs):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro(*args, **kwargs))
        finally:
            loop.close()
    return wrapper

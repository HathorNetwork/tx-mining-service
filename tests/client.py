"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""

import asyncio
import unittest
from unittest.mock import MagicMock, Mock

from txstratum.commons.client import HathorClient


class ClientTestCase(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        self.client = HathorClient(server_url='')
        self.loop.run_until_complete(self.client.start())

    def _run_all_pending_events(self):
        """Run all pending events."""
        # pending = asyncio.all_tasks(self.loop)
        # self.loop.run_until_complete(asyncio.gather(*pending))
        async def _fn():
            pass
        future = asyncio.ensure_future(_fn())
        self.loop.run_until_complete(future)

    def test_version(self):
        self.client._session = Mock()
        self.client._session.get = MagicMock(return_value=None)

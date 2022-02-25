from asyncore import loop

import asynctest
from aiohttp import web
from aiohttp.test_utils import (
    AioHTTPTestCase,
    TestClient,
    TestServer,
    unittest_run_loop,
)

from txstratum.rate_limiter import MemoryLimiter, get_default_keyfunc

limiter = MemoryLimiter()


class FakeApp:
    def __init__(self):
        self.app = web.Application()
        self.app.router.add_get("/route", self.route)

    @limiter.limit(keyfunc=get_default_keyfunc("1/3"))
    def route(self, request: web.Request) -> web.Response:
        return web.json_response({"success": True})


class RateLimiterTestCase(asynctest.ClockedTestCase):
    def setUp(self):
        from tests.utils import Clock

        self.clock = Clock(self.loop)
        self.clock.enable()

        self.app = FakeApp().app
        self.server = TestServer(self.app, loop=self.loop)
        self.client = TestClient(self.server, loop=self.loop)

        self.loop.run_until_complete(self.client.start_server())

    def tearDown(self):
        self.loop.run_until_complete(self.client.close())

        self.clock.disable()

    async def test_rate_limit(self):
        # First call should be allowed
        resp = await self.client.request("GET", "/route")
        assert resp.status == 200
        data = await resp.json()
        self.assertTrue(data["success"])

        # Second call should be rate limited
        resp = await self.client.request("GET", "/route")
        assert resp.status == 429
        data = await resp.json()
        self.assertEqual(data["error"], "Rate limit exceeded: 1 per 3 second")

        # TODO How to make time pass?

        # # Third call should be allowed after 3 seconds
        # await self.advance(3)

        # resp = await self.client.request('GET', '/route')
        # assert resp.status == 200
        # data = await resp.json()
        # self.assertTrue(data['success'])

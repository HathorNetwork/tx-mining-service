from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from txstratum.rate_limiter import MemoryLimiter, get_default_keyfunc


class FakeApp:
    def __init__(self):
        self.limiter = MemoryLimiter()

        self.app = web.Application()
        self.app.router.add_get(
            "/route", self.limiter.limit(keyfunc=get_default_keyfunc("1/3"))(self.route)
        )

    async def route(self, request: web.Request) -> web.Response:
        return web.json_response({"success": True})

    def clear(self):
        self.limiter.db.reset()


class RateLimiterTestCase(AioHTTPTestCase):
    async def get_application(self):
        self.fake_app = FakeApp()
        return self.fake_app.app

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

        # Clear the rate limiter
        self.fake_app.clear()

        # Third call should be allowed
        resp = await self.client.request("GET", "/route")
        assert resp.status == 200
        data = await resp.json()
        self.assertTrue(data["success"])

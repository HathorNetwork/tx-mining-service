# TODO: Add copyright?

import asyncio
import json
from abc import ABC, abstractmethod
from functools import wraps
from typing import Awaitable, Callable, Optional, Tuple, Type, Union

import limits.strategies
from aiohttp.web import Request, Response
from limits.storage import MemoryStorage, RedisStorage, Storage


def get_default_keyfunc(ratelimit: str) -> Callable[[str], str]:
    def keyfunc(request: Request) -> Tuple[str, str]:
        """
        Returns the user's IP as key and the same rate limit for all IPs.
        """
        ip = request.headers.get("X-Forwarded-For") or request.remote or "127.0.0.1"
        ip = ip.split(",")[0]
        return ip, ratelimit

    return keyfunc


class Allow:
    def __init__(self) -> None:
        pass


class RateLimitExceeded:
    def __init__(self, detail: str) -> None:
        self._detail = detail

    @property
    def detail(self):
        return self._detail


class BaseRateLimitDecorator:
    """
    Decorator to rate limit requests in the aiohttp.web framework
    """

    def __init__(
        self,
        db: Storage,
        path_id: str,
        moving_window: limits.strategies.MovingWindowRateLimiter,
        keyfunc: Callable,
        error_handler: Optional[Union[Callable, Awaitable]] = None,
    ) -> None:
        self.keyfunc = keyfunc
        self.error_handler = error_handler
        self.db = db
        self.path_id = path_id
        self.moving_window = moving_window
        self.items = {}

    def create_or_get_item(self, key: str, ratelimit: str) -> limits.RateLimitItem:
        if key in self.items:
            return self.items[key]

        calls, period = ratelimit.split("/")
        calls = int(calls)
        period = int(period)
        assert period > 0
        assert calls > 0

        if period >= 31_536_000:
            item = limits.RateLimitItemPerYear(calls, period / 31_536_000)
        elif period >= 2_628_000:
            item = limits.RateLimitItemPerMonth(calls, period / 2_628_000)
        elif period >= 86400:
            item = limits.RateLimitItemPerDay(calls, period / 86400)
        elif period >= 3600:
            item = limits.RateLimitItemPerHour(calls, period / 3600)
        elif period >= 60:
            item = limits.RateLimitItemPerMinute(calls, period / 60)
        else:
            item = limits.RateLimitItemPerSecond(calls, period)

        self.items[key] = item

        return item

    def get_item_for_key(self, key: str) -> limits.RateLimitItem:
        if not key in self.items:
            # TODO Issue a warning about this?
            return self.items["default"]

        return self.items[key]

    def __call__(self, func: Callable) -> Awaitable:
        @wraps(func)
        async def wrapper(request: Request) -> Response:
            key, ratelimit = self.keyfunc(request)
            db_key = f"{key}:{self.path_id or request.path}"

            item = self.create_or_get_item(db_key, ratelimit)

            if isinstance(self.db, MemoryStorage):
                if not self.db.check():
                    self.db.reset()

            assert asyncio.iscoroutinefunction(func), "func must be a coroutine"

            # Returns a response if the number of calls exceeds the max amount of calls
            if not self.moving_window.test(item, db_key):
                if self.error_handler is not None:
                    if asyncio.iscoroutinefunction(self.error_handler):
                        r = await self.error_handler(
                            request, RateLimitExceeded(**{"detail": str(item)})
                        )
                        if isinstance(r, Allow):
                            return await func(request)
                        return r
                    else:
                        r = self.error_handler(
                            request, RateLimitExceeded(**{"detail": str(item)})
                        )
                        if isinstance(r, Allow):
                            return await func(request)
                        return r
                data = json.dumps({"error": f"Rate limit exceeded: {item}"})
                response = Response(
                    text=data, content_type="application/json", status=429
                )
                response.headers.add("error", f"Rate limit exceeded: {item}")
                return response

            # Registers new hit and returns normal response if the user did not go over the rate limit
            self.moving_window.hit(item, db_key)
            return await func(request)

        return wrapper


class RateLimiter(ABC):
    @abstractmethod
    def limit(
        self,
        keyfunc: Callable,
        error_handler: Optional[Union[Callable, Awaitable]],
        path_id: Optional[str],
    ) -> Callable:
        raise NotImplementedError


class MemoryLimiter(RateLimiter):
    """
    ```
    limiter = MemoryLimiter(keyfunc=your_keyfunc)

    @routes.get("/")
    @limiter.limit(keyfunc=get_default_keyfunc("1/3"))
    def foo():
        return Response(text="Hello World")
    ```
    """

    def __init__(
        self, error_handler: Optional[Union[Callable, Awaitable]] = None
    ) -> None:
        self.error_handler = error_handler
        self.db = MemoryStorage()
        self.moving_window = limits.strategies.MovingWindowRateLimiter(self.db)

    def limit(
        self,
        keyfunc: Callable,
        error_handler: Optional[Union[Callable, Awaitable]] = None,
        path_id: str = None,
    ) -> Callable:
        def wrapper(func: Callable) -> Awaitable:
            _error_handler = self.error_handler or error_handler

            return BaseRateLimitDecorator(
                keyfunc=keyfunc,
                error_handler=_error_handler,
                db=self.db,
                path_id=path_id,
                moving_window=self.moving_window,
            )(func)

        return wrapper


class RedisLimiter(RateLimiter):
    """
    ```
    limiter = RedisLimiter(keyfunc=your_keyfunc, uri="redis://username:password@host:port")
    @routes.get("/")
    @limiter.limit(keyfunc=get_default_keyfunc("1/3"))
    def foo():
        return Response(text="Hello World")
    ```
    """

    def __init__(
        self, uri: str, error_handler: Optional[Union[Callable, Awaitable]] = None
    ) -> None:
        self.db = RedisStorage(uri=uri)
        self.moving_window = limits.strategies.MovingWindowRateLimiter(self.db)
        self.error_handler = error_handler

    def limit(
        self,
        keyfunc: Callable = None,
        error_handler: Optional[Union[Callable, Awaitable]] = None,
        path_id: str = None,
    ) -> Callable:
        def wrapper(func: Callable) -> Awaitable:
            _error_handler = self.error_handler or error_handler

            return BaseRateLimitDecorator(
                db=self.db,
                keyfunc=keyfunc,
                error_handler=_error_handler,
                path_id=path_id,
                moving_window=self.moving_window,
            )(func)

        return wrapper

# TODO: Add copyright?

import asyncio
import json
from abc import ABC, abstractmethod
from functools import wraps
from typing import Awaitable, Callable, Dict, Optional, Tuple

import limits.strategies
from aiohttp.web import Request, Response
from limits.storage import MemoryStorage, RedisStorage


class Allow:
    """This class can be used by custom error handlers passed to the rate limiter to allow the request to continue."""

    def __init__(self) -> None:  # noqa: D107
        pass


class RateLimitExceeded:
    """This class is used to pass the details of a rate limit exceeded error to the error handler."""

    def __init__(self, detail: str) -> None:  # noqa: D107
        self._detail = detail

    @property
    def detail(self) -> str:  # noqa: D102
        return self._detail


# Type aliases
keyfunc_type = Callable[[Request], Tuple[str, str]]
func_type = Callable[[Request], Awaitable[Response]]
error_handler_type = Optional[
    Callable[[Request, RateLimitExceeded], Awaitable[Response]]
]
wrapper_type = Callable[[Request], Awaitable[Response]]


def get_default_keyfunc(ratelimit: str) -> keyfunc_type:
    """
    Return the default keyfunc to be used with the rate limiter.

    The keyfunc is used by the rate limiter to determine the key and rate limit to apply to a request.

    The default keyfunc uses the user's IP as key and the same rate limit for all IPs.
    """

    def keyfunc(request: Request) -> Tuple[str, str]:
        ip = request.headers.get("X-Forwarded-For") or request.remote or "127.0.0.1"
        ip = ip.split(",")[0]
        return ip, ratelimit

    return keyfunc


class BaseRateLimitDecorator:
    """Decorator to rate limit requests in the aiohttp.web framework."""

    def __init__(
        self,
        path_id: Optional[str],
        moving_window: limits.strategies.MovingWindowRateLimiter,
        keyfunc: keyfunc_type,
        error_handler: error_handler_type = None,
    ) -> None:
        """Init method.

        :param path_id: This is included in the key. Usually the path of the request is passed here.
        :param moving_window: The rate limiting moving window to use. It should have been initialized with the storage.
        :param keyfunc: The keyfunc to use for the rate limiter.
            It is used to determine the key and rate limit to apply to a request.
        :param error_handler: Custom error handler, defaults to None
        """
        self.keyfunc = keyfunc
        self.error_handler = error_handler
        self.path_id = path_id
        self.moving_window = moving_window
        self.items: Dict[str, limits.RateLimitItem] = {}

    def create_or_get_item(self, key: str, ratelimit: str) -> limits.RateLimitItem:
        """Create a new rate limit item if it does not exist and returns it.

        :param key: The key to use for the rate limit item.
        :param ratelimit: The rate limit to use for the rate limit item.
        :return: The rate limit item.
        """
        if key in self.items:
            return self.items[key]

        c, p = ratelimit.split("/")
        calls = int(c)
        period = int(p)

        assert period > 0
        assert calls > 0

        item = limits.RateLimitItemPerSecond(calls, period)

        self.items[key] = item

        return item

    def __call__(self, func: func_type) -> wrapper_type:
        """Wrap an aiohttp route function to apply the rate limiting.

        :param func: The function to wrap.
        :return: The wrapped function.
        """

        @wraps(func)
        async def wrapper(request: Request) -> Response:
            key, ratelimit = self.keyfunc(request)
            db_key = f"{key}:{self.path_id or request.path}"

            item = self.create_or_get_item(db_key, ratelimit)

            assert asyncio.iscoroutinefunction(func), "func must be a coroutine"

            # Returns a response if the number of calls exceeds the max amount of calls
            if not self.moving_window.test(item, db_key):
                if self.error_handler is not None:
                    assert asyncio.iscoroutinefunction(
                        self.error_handler
                    ), "error_handler must be a coroutine"

                    r = await self.error_handler(
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
    """Interface for rate limiter implementations."""

    @abstractmethod
    def limit(
        self,
        keyfunc: keyfunc_type,
        error_handler: error_handler_type = None,
        path_id: Optional[str] = None,
    ) -> Callable[[func_type], wrapper_type]:
        """Return a decorator to be used in aiohttp.web routes.

        :param keyfunc: The keyfunc to use for the rate limiter.
            It is used to determine the key and rate limit to apply to a request.
        :param error_handler: Custom error handler, defaults to None
        :param path_id: This is included in the key. Usually the path of the request is passed here.
        :return: The decorator.
        """
        raise NotImplementedError


class MemoryLimiter(RateLimiter):
    """Rate Limiter class that uses Memory as storage.

    Example usage:
    ```
    limiter = MemoryLimiter(keyfunc=your_keyfunc)

    @routes.get("/")
    @limiter.limit(keyfunc=get_default_keyfunc("1/3"))
    def foo():
        return Response(text="Hello World")
    ```
    """

    def __init__(self, error_handler: error_handler_type = None) -> None:
        """Initilize the MemoryLimiter.

        :param error_handler: Custom error handler, defaults to None
        """
        self.error_handler = error_handler
        self.db = MemoryStorage()
        self.moving_window = limits.strategies.MovingWindowRateLimiter(self.db)

        if not self.db.check():
            self.db.reset()  # type: ignore[no-untyped-call]

    def limit(
        self,
        keyfunc: keyfunc_type,
        error_handler: error_handler_type = None,
        path_id: Optional[str] = None,
    ) -> Callable[[func_type], wrapper_type]:
        """Return a decorator to be used in aiohttp.web routes.

        :param keyfunc: The keyfunc to use for the rate limiter.
            It is used to determine the key and rate limit to apply to a request.
        :param error_handler: Custom error handler, defaults to None
        :param path_id: This is included in the key. Usually the path of the request is passed here.
        :return: The decorator.
        """

        def wrapper(func: func_type) -> wrapper_type:
            _error_handler = self.error_handler or error_handler

            return BaseRateLimitDecorator(
                keyfunc=keyfunc,
                error_handler=_error_handler,
                path_id=path_id,
                moving_window=self.moving_window,
            )(func)

        return wrapper


class RedisLimiter(RateLimiter):
    """Rate Limter class that uses Redis as storage.

    ```
    limiter = RedisLimiter(keyfunc=your_keyfunc, uri="redis://username:password@host:port")
    @routes.get("/")
    @limiter.limit(keyfunc=get_default_keyfunc("1/3"))
    def foo():
        return Response(text="Hello World")
    ```
    """

    def __init__(self, uri: str, error_handler: error_handler_type = None) -> None:
        """Initilize the RedisLimiter.

        :param uri: The URI to connect to the redis server.
        :param error_handler: Custom error handler, defaults to None
        """
        self.db = RedisStorage(uri=uri)
        self.moving_window = limits.strategies.MovingWindowRateLimiter(self.db)
        self.error_handler = error_handler

    def limit(
        self,
        keyfunc: keyfunc_type,
        error_handler: error_handler_type = None,
        path_id: Optional[str] = None,
    ) -> Callable[[func_type], wrapper_type]:
        """Return a decorator to be used in aiohttp.web routes.

        :param keyfunc: The keyfunc to use for the rate limiter.
            It is used to determine the key and rate limit to apply to a request.
        :param error_handler: Custom error handler, defaults to None
        :param path_id: This is included in the key. Usually the path of the request is passed here.
        :return: The decorator.
        """

        def wrapper(func: func_type) -> wrapper_type:
            _error_handler = self.error_handler or error_handler

            return BaseRateLimitDecorator(
                keyfunc=keyfunc,
                error_handler=_error_handler,
                path_id=path_id,
                moving_window=self.moving_window,
            )(func)

        return wrapper

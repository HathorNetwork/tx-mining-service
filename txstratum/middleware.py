# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import json
import re
from typing import Any, Awaitable, Callable, Optional, Pattern

from aiohttp import web
from aiohttp.typedefs import Handler
from structlog import get_logger

from txstratum.utils import is_version_gte

logger = get_logger()

# Middleware factory return type
Middleware = Callable[[web.Request, Handler], Awaitable[web.StreamResponse]]

# Error message
VERSION_CHECK_ERROR_MESSAGE = "wallet-version-too-old"


def get_real_ip(request: web.Request) -> Optional[str]:
    """Return the real client IP, preferring X-Forwarded-For over the direct peer.

    Behind a load balancer / reverse proxy ``request.remote`` is the proxy's IP;
    the original client is the first entry of the X-Forwarded-For header.
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote


def create_error_middleware(log: Any) -> Middleware:
    """Middleware that logs every unhandled exception / 5xx with request context.

    aiohttp turns an uncaught handler exception into a bare HTTP 500 with no
    structured, attributable log. This wrapper records the method, path, client
    (User-Agent + real IP) and the exception+traceback before re-raising, so a
    client-side "An unexpected error happened" always has a server-side trace.
    """

    @web.middleware
    async def error_logging(
        request: web.Request, handler: Handler
    ) -> web.StreamResponse:
        try:
            return await handler(request)
        except web.HTTPException as http_exc:
            # Responses raised on purpose. Only 5xx are worth an error log here.
            if http_exc.status >= 500:
                log.error(
                    "request-http-exception",
                    status=http_exc.status,
                    method=request.method,
                    path=request.path,
                    user_agent=request.headers.get("User-Agent"),
                    real_ip=get_real_ip(request),
                )
            raise
        except Exception as e:
            log.exception(
                "unhandled-request-error",
                method=request.method,
                path=request.path,
                user_agent=request.headers.get("User-Agent"),
                real_ip=get_real_ip(request),
                exception=repr(e),
            )
            raise web.HTTPInternalServerError(
                text=json.dumps({"error": "internal-error"}),
                content_type="application/json",
            )

    return error_logging

# Compiled wallet regex
# We should compile them outside the function to improve
# the middleware performance

# Example of a wallet desktop user agent
# Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) HathorWallet/0.22.1
# Chrome/73.0.3683.121 Electron/5.0.13 Safari/537.36 HathorWallet/0.22.1
WALLET_DESKTOP_COMPILED_REGEX = re.compile(r"HathorWallet/(\d+\.\d+.\d+)")

# Example of a wallet mobile user agent
# Hathor Wallet Mobile / 0.18.0
WALLET_MOBILE_COMPILED_REGEX = re.compile(r"Hathor Wallet Mobile / (\d+\.\d+\.\d+)")

# Example of previous versions user agent for mobile
# HathorMobile/1
WALLET_MOBILE_CUSTOM_COMPILED_REGEX = re.compile(r"HathorMobile/1")

# Example of a wallet headless user agent
# Hathor Wallet Headless / 0.14.0
WALLET_HEADLESS_COMPILED_REGEX = re.compile(r"Hathor Wallet Headless / (\d+\.\d+\.\d+)")

# The header key that is used to ignore the wallet version check
# Issue: https://github.com/HathorNetwork/internal-issues/issues/118
HEADER_SKIP_VERSION_CHECK_KEY = "X-UNSAFE-IGNORE-WALLET-VERSION-CHECK"

# The header value that is used to ignore the wallet version check
HEADER_SKIP_VERSION_CHECK_VALUE = (
    "I know what I am doing and I want to skip wallet version check"
)


def create_middleware_version_check(
    min_wallet_desktop_version: Optional[str],
    min_wallet_mobile_version: Optional[str],
    min_wallet_headless_version: Optional[str],
) -> Middleware:
    """Middleware factory."""

    @web.middleware
    async def version_check(
        request: web.Request, handler: Handler
    ) -> web.StreamResponse:
        """Check wallet versions from user agent."""
        skip_version_check_header = request.headers.get(HEADER_SKIP_VERSION_CHECK_KEY)
        if skip_version_check_header == HEADER_SKIP_VERSION_CHECK_VALUE:
            response = await handler(request)
            return response

        user_agent = request.headers.get("User-Agent")

        if not user_agent:
            response = await handler(request)
            return response

        if min_wallet_desktop_version:
            # Search user agent for wallet desktop string and get version
            # Then check if version is the minimum allowed
            version = get_version_from_user_agent(
                WALLET_DESKTOP_COMPILED_REGEX, user_agent
            )
            if version and not is_version_gte(version, min_wallet_desktop_version):
                logger.warning(
                    "wallet-version-rejected",
                    wallet_type="desktop",
                    detected_version=version,
                    min_version=min_wallet_desktop_version,
                    user_agent=user_agent,
                    real_ip=get_real_ip(request),
                    path=request.path,
                )
                return web.json_response(
                    {
                        "error": VERSION_CHECK_ERROR_MESSAGE,
                        "data": {
                            "min_version": min_wallet_desktop_version,
                            "version": version,
                        },
                    },
                    status=400,
                )

        if min_wallet_mobile_version:
            # Search user agent for wallet mobile string and get version
            # Then check if version is the minimum allowed
            version = get_version_from_user_agent(
                WALLET_MOBILE_COMPILED_REGEX, user_agent
            )
            if version and not is_version_gte(version, min_wallet_mobile_version):
                logger.warning(
                    "wallet-version-rejected",
                    wallet_type="mobile",
                    detected_version=version,
                    min_version=min_wallet_mobile_version,
                    user_agent=user_agent,
                    real_ip=get_real_ip(request),
                    path=request.path,
                )
                return web.json_response(
                    {
                        "error": VERSION_CHECK_ERROR_MESSAGE,
                        "data": {
                            "min_version": min_wallet_mobile_version,
                            "version": version,
                        },
                    },
                    status=400,
                )

            # Before version 0.18.0 in the wallet mobile, the user agent was receiving a fixed "version"
            # the user agent was "HathorMobile/1", so if the min version is at least 0.18.0, then
            # we must have a custom check here for it
            if is_version_gte(min_wallet_mobile_version, "0.18.0"):
                search = WALLET_MOBILE_CUSTOM_COMPILED_REGEX.search(user_agent)
                if search:
                    # If the regex found it, then we should block
                    logger.warning(
                        "wallet-version-rejected",
                        wallet_type="mobile-legacy",
                        detected_version="HathorMobile/1",
                        min_version=min_wallet_mobile_version,
                        user_agent=user_agent,
                        real_ip=get_real_ip(request),
                        path=request.path,
                    )
                    return web.json_response(
                        {
                            "error": VERSION_CHECK_ERROR_MESSAGE,
                            "data": {"min_version": min_wallet_mobile_version},
                        },
                        status=400,
                    )

        if min_wallet_headless_version:
            # Seach user agent for wallet headless string and get version
            # Then check if version is the minimum allowed
            version = get_version_from_user_agent(
                WALLET_HEADLESS_COMPILED_REGEX, user_agent
            )
            if version and not is_version_gte(version, min_wallet_headless_version):
                logger.warning(
                    "wallet-version-rejected",
                    wallet_type="headless",
                    detected_version=version,
                    min_version=min_wallet_headless_version,
                    user_agent=user_agent,
                    real_ip=get_real_ip(request),
                    path=request.path,
                )
                return web.json_response(
                    {
                        "error": VERSION_CHECK_ERROR_MESSAGE,
                        "data": {
                            "min_version": min_wallet_headless_version,
                            "version": version,
                        },
                    },
                    status=400,
                )

        response = await handler(request)
        return response

    return version_check


def get_version_from_user_agent(
    compiled_regex: Pattern[str], user_agent: str
) -> Optional[str]:
    """Parse user agent using regex to search for a wallet version on it."""
    search = compiled_regex.search(user_agent)
    if search and search.groups():
        return search.groups()[0]

    return None

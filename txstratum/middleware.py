# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import re
from typing import Awaitable, Callable, Optional, Pattern

from aiohttp import web
from aiohttp.typedefs import Handler

from txstratum.utils import is_version_gte

# Middleware factory return type
Middleware = Callable[[web.Request, Handler], Awaitable[web.StreamResponse]]

# Error message
VERSION_CHECK_ERROR_MESSAGE = "wallet-version-too-old"

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

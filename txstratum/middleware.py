# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import re
from typing import Callable, Optional

from aiohttp import web
from pkg_resources import packaging

handler_callable = Callable[[web.Request], web.Response]


def create_middleware_version_check(
    min_wallet_desktop_version: Optional[str],
    min_wallet_mobile_version: Optional[str],
    min_wallet_headless_version: Optional[str],
) -> Callable[[web.Request, handler_callable], web.Response]:
    """Middleware factory."""

    @web.middleware
    async def version_check(
        request: web.Request, handler: handler_callable
    ) -> web.Response:
        """Check wallet versions from user agent."""
        user_agent = request.headers.get("User-Agent")

        if min_wallet_desktop_version:
            # Search user agent for wallet desktop string and get version
            # Then check if version is the minimum allowed
            # Example of a wallet desktop user agent
            # Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) HathorWallet/0.22.1
            # Chrome/73.0.3683.121 Electron/5.0.13 Safari/537.36 HathorWallet/0.22.1
            wallet_desktop_regex = r"HathorWallet/(\d+\.\d+.\d+)"
            if is_version_invalid(
                wallet_desktop_regex, user_agent, min_wallet_desktop_version
            ):
                return web.json_response(
                    {"error": "wallet-version-invalid"}, status=400
                )

        if min_wallet_mobile_version:
            # Search user agent for wallet mobile string and get version
            # Then check if version is the minimum allowed
            # Example of a wallet mobile user agent
            # Hathor Wallet Mobile / 0.18.0
            wallet_mobile_regex = r"Hathor Wallet Mobile / (\d+\.\d+\.\d+)"
            if is_version_invalid(
                wallet_mobile_regex, user_agent, min_wallet_mobile_version
            ):
                return web.json_response(
                    {"error": "wallet-version-invalid"}, status=400
                )

            # Before version 0.18.0 in the wallet mobile, the user agent was receiving a fixed "version"
            # the user agent was "HathorMobile/1", so if the min version is at least 0.18.0, then
            # we must have a custom check here for it
            if packaging.version.parse(
                min_wallet_mobile_version
            ) >= packaging.version.parse("0.18.0"):
                custom_regex = "HathorMobile/1"
                search = re.search(custom_regex, user_agent)
                if search:
                    # If the regex found it, then we should block
                    return web.json_response(
                        {"error": "wallet-version-invalid"}, status=400
                    )

        if min_wallet_headless_version:
            # Seach user agent for wallet headless string and get version
            # Then check if version is the minimum allowed
            # Example of a wallet headless user agent
            # Hathor Wallet Headless / 0.14.0
            wallet_headless_regex = r"Hathor Wallet Headless / (\d+\.\d+\.\d+)"
            if is_version_invalid(
                wallet_headless_regex, user_agent, min_wallet_headless_version
            ):
                return web.json_response(
                    {"error": "wallet-version-invalid"}, status=400
                )

        return await handler(request)

    return version_check


def is_version_invalid(regex: str, user_agent: str, min_version: str) -> bool:
    """Check if version in user agent is invalid comparing with min version.

    Regex is used to get the version from user agent
    """
    search = re.search(regex, user_agent)
    if search and search.groups():
        version = search.groups()[0]
        return packaging.version.parse(version) < packaging.version.parse(min_version)

    return False

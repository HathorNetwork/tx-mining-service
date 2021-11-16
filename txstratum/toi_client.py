# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from aiohttp import ClientSession
from structlog import get_logger

logger = get_logger()


@dataclass
class CheckBlacklist:
    """Class to parse return from TOI Service /check/blacklist/ API."""

    blacklisted: bool
    issues: Optional[Dict[str, Any]] = None


class TOIAsyncClient:
    """TOI Service async client based on aiohttp.ClientSession."""

    def __init__(
            self,
            base_url: str,
            apikey: Optional[str] = None,
            session: Optional[ClientSession] = None,
    ) -> None:
        """Configure TOIAsyncClient session and auth.

        base_url: TOI Service host
        apikey: TOI Service apikey for auth (optional)
        session: Use this preconfigured session (optional)
        """
        self.base_url = base_url
        self.apikey = apikey
        if session is None:
            assert apikey
            self.session = self.make_session(apikey)
        else:
            self.session = session
        self.log = logger.new()

    @classmethod
    def make_session(self, apikey: str) -> ClientSession:
        """Create aiohttp.ClientSession with auth headers."""
        return ClientSession(headers={'X-APIKEY': apikey})

    def endpoint(self, path: str) -> str:
        """Build an URL from path."""
        return urljoin(self.base_url, path)

    async def close(self) -> None:
        """Close the underlying aiohttp.ClientSession."""
        if self.session and not self.session.closed:
            await self.session.close()

    async def check_blacklist(
            self, *,
            tx_ids: Optional[List[str]] = None,
            addresses: Optional[List[str]] = None,
            reasons: Optional[List[str]] = None) -> CheckBlacklist:
        """Check if any of `tx_ids` and `addresses` are banned."""
        endpoint = self.endpoint('check/blacklist/')
        reasons = reasons or ['attack', 'legal', 'ban']
        params = {'reasons': reasons}
        if tx_ids:
            params['tx_id'] = tx_ids
        if addresses:
            params['address'] = addresses
        async with self.session.get(endpoint, params=params) as response:
            if response.status == 200:
                r = await response.json()
                return CheckBlacklist(**r)
            self.log.warning(
                message='TOI Service unexpected response',
                status=response.status,
                body=await response.text(),
            )
            # XXX: Allow if service fails call
            return CheckBlacklist(blacklisted=False)

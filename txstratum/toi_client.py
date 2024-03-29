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


class TOIError(Exception):
    """Raised upon any unexpected responses from toi service."""

    pass


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
        return ClientSession(headers={"X-APIKEY": apikey})

    def endpoint(self, path: str) -> str:
        """Build an URL from path."""
        return urljoin(self.base_url, path)

    async def close(self) -> None:
        """Close the underlying aiohttp.ClientSession."""
        if self.session and not self.session.closed:
            await self.session.close()

    async def check_blacklist(
        self,
        *,
        tx_ids: Optional[List[str]] = None,
        addresses: Optional[List[str]] = None,
        reasons: Optional[List[str]] = None
    ) -> CheckBlacklist:
        """Check if any of `tx_ids` and `addresses` are banned."""
        endpoint = self.endpoint("check/blacklist/v2")
        reasons = reasons or ["attack", "legal", "ban"]
        data = {"reasons": reasons}
        if tx_ids:
            data["tx_ids"] = tx_ids
        if addresses:
            data["addresses"] = addresses
        async with self.session.post(endpoint, json=data) as response:
            if response.status == 200:
                r = await response.json()
                return CheckBlacklist(**r)
            body = await response.text()
            self.log.warning(
                "TOI Service unexpected response",
                status=response.status,
                body=body,
            )
            raise TOIError(body)

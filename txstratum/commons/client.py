# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
from typing import Any, Dict, List, NamedTuple, Optional, cast
from urllib.parse import urljoin

from aiohttp import ClientSession
from structlog import get_logger

from txstratum.commons import Block, TxOutput

REQUIRED_HATHOR_API_VERSION = 'v1a'

logger = get_logger()


class BlockTemplate(NamedTuple):
    """Block template."""

    data: bytes
    height: int

    def to_dict(self) -> Dict[str, Any]:
        """Return dict for json serialization."""
        return {
            'data': self.data.hex(),
            'height': self.height,
        }


class HathorVersion(NamedTuple):
    """Hathor backend version."""

    major: int
    minor: int
    patch: int


class HathorClient:
    """Used to communicate with Hathor's full-node."""

    USER_AGENT = 'tx-mining-service'

    def __init__(self, server_url: str, api_version: str = REQUIRED_HATHOR_API_VERSION):
        """Init HathorClient with a Hathor's full-node backend."""
        self.log = logger.new()
        self._base_url = urljoin(server_url, api_version).rstrip('/') + '/'
        self._base_headers = {
            'User-Agent': self.USER_AGENT,
        }
        self._session: Optional[ClientSession] = None

    async def start(self) -> None:
        """Start a session with the backend."""
        self._session = ClientSession(headers=self._base_headers)

    async def stop(self) -> None:
        """Stop a session with the backend."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _get_url(self, url: str) -> str:
        return urljoin(self._base_url, url.lstrip('/'))

    async def version(self) -> HathorVersion:
        """Return the version of the backend."""
        assert self._session is not None
        async with self._session.get(self._get_url('version')) as resp:
            data = await resp.json()
            ver = data['version']
            major, minor, patch = ver.split('.')
            return HathorVersion(int(major), int(minor), int(patch))

    async def get_block_template(self, address: Optional[str] = None) -> BlockTemplate:
        """Return a block template."""
        assert self._session is not None
        params = {}
        if address is not None:
            params['address'] = address
        async with self._session.get(self._get_url('get_block_template'), params=params) as resp:
            data = await resp.json()

            if data.get('error'):
                self.log.error('Error getting block template', data=data)
                raise RuntimeError('Cannot get block template')

            # Get height.
            metadata = data.get('metadata', {})
            height = metadata['height']

            # Build block.
            blk = Block()
            blk.version = 0
            blk.timestamp = data['timestamp']
            blk.weight = data['weight']
            blk.parents = [bytes.fromhex(x) for x in data['parents']]
            blk.data = b''

            do = data['outputs'][0]
            txout = TxOutput(
                value=do['value'],
                token_data=0,
                script=b'',
            )
            blk.outputs = [txout]
            return BlockTemplate(data=bytes(blk), height=height)

    async def get_tx_parents(self) -> List[bytes]:
        """Return parents for a new transaction."""
        assert self._session is not None
        async with self._session.get(self._get_url('tx_parents')) as resp:
            data = await resp.json()
            if not data.get('success'):
                raise RuntimeError('Cannot get tx parents')
            return [bytes.fromhex(x) for x in data['tx_parents']]

    async def push_tx_or_block(self, raw: bytes) -> bool:
        """Push a new tx or block to the backend."""
        assert self._session is not None
        data = {
            'hexdata': raw.hex(),
        }
        async with self._session.post(self._get_url('submit_block'), json=data) as resp:
            return cast(bool, (await resp.json())['result'])

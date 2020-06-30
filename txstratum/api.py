# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import json
from typing import TYPE_CHECKING, Optional

from aiohttp import web
from txstratum.commons import TokenCreationTransaction, Transaction
from txstratum.jobs import MinerTxJob
from txstratum.utils import tx_or_block_from_bytes

if TYPE_CHECKING:
    from txstratum.manager import TxMiningManager


# Default maximum tx weight allowed to be mined in this server.
MAX_TX_WEIGHT = 35


# Tx timeout (seconds)
TX_TIMEOUT = 20


class App:
    """API used to manage tx job."""

    def __init__(self, manager: 'TxMiningManager', *, max_tx_weight: Optional[int] = None,
                 tx_timeout: Optional[int] = None):
        """Init App."""
        super().__init__()
        self.manager = manager
        self.max_tx_weight = max_tx_weight or MAX_TX_WEIGHT
        self.tx_timeout = tx_timeout or TX_TIMEOUT
        self.app = web.Application()
        self.app.router.add_get('/health-check', self.health_check)
        self.app.router.add_get('/mining-status', self.mining_status)
        self.app.router.add_get('/job-status', self.job_status)
        self.app.router.add_post('/submit-job', self.submit_job)
        self.app.router.add_post('/cancel-job', self.cancel_job)

    async def health_check(self, request: web.Request) -> web.Response:
        """Return that the service is running."""
        return web.json_response({'success': True})

    async def mining_status(self, request: web.Request) -> web.Response:
        """Return status of miners."""
        return web.json_response(self.manager.status())

    async def submit_job(self, request: web.Request) -> web.Response:
        """Submit a new tx job to the manager.

        Method: POST
        Format: json
        Params:
        - tx: str, hex dump of the transaction
        - propagate: bool, propagate tx to Hathor’s full node after it is solved
        - add_parents: bool, add parents before resolving the tx
        """
        try:
            data = await request.json()
        except json.decoder.JSONDecodeError:
            return web.json_response({'error': 'cannot-decode-json'}, status=400)
        if not isinstance(data, dict):
            return web.json_response({'error': 'json-must-be-an-object'}, status=400)
        tx_hex = data.get('tx')
        if not tx_hex:
            return web.json_response({'error': 'missing-tx'}, status=400)
        try:
            tx_bytes = bytes.fromhex(tx_hex)
            tx = tx_or_block_from_bytes(tx_bytes)
        except ValueError:
            return web.json_response({'error': 'invalid-tx'}, status=400)

        if not isinstance(tx, (Transaction, TokenCreationTransaction)):
            return web.json_response({'error': 'invalid-tx'}, status=400)

        if tx.weight > self.max_tx_weight:
            return web.json_response({'error': 'tx-weight-is-too-high'}, status=400)

        add_parents = data.get('add_parents', False)
        propagate = data.get('propagate', False)
        job = MinerTxJob(tx_bytes, add_parents=add_parents, propagate=propagate, timeout=self.tx_timeout)
        success = self.manager.add_job(job)
        if not success:
            return web.json_response({'error': 'job-already-exists'}, status=400)
        return web.json_response(job.to_dict())

    def _get_job(self, uuid_hex: Optional[str]) -> MinerTxJob:
        """Return job from uuid_hex. It raises web exceptions for common issues."""
        if not uuid_hex:
            raise web.HTTPBadRequest(body=json.dumps({'error': 'missing-job-id'}), content_type='application/json')
        try:
            uuid = bytes.fromhex(uuid_hex)
        except ValueError:
            raise web.HTTPBadRequest(body=json.dumps({'error': 'invalid-uuid'}), content_type='application/json')
        job = self.manager.get_job(uuid)
        if job is None:
            raise web.HTTPNotFound(body=json.dumps({'error': 'job-not-found'}), content_type='application/json')
        return job

    async def job_status(self, request: web.Request) -> web.Response:
        """Get the status of a tx job.

        Method: GET
        Query:
        - job-id: str, job identifier
        """
        job = self._get_job(request.query.get('job-id'))
        return web.json_response(job.to_dict())

    async def cancel_job(self, request: web.Request) -> web.Response:
        """Cancel a tx job.

        Method: POST
        Query:
        - job-id: str, job identifier
        """
        job = self._get_job(request.query.get('job-id'))
        self.manager.cancel_job(job)
        return web.json_response({'cancelled': True, 'job-id': job.uuid.hex()})

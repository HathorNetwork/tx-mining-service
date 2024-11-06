# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import json
from struct import error as StructError
from typing import TYPE_CHECKING, List, Optional

from aiohttp import web
from hathorlib import TokenCreationTransaction, Transaction
from hathorlib.base_transaction import tx_or_block_from_bytes
from hathorlib.exceptions import TxValidationError
from structlog import get_logger

import txstratum.time
from txstratum.exceptions import JobAlreadyExists, NewJobRefused
from txstratum.healthcheck.healthcheck import HealthCheck
from txstratum.jobs import JobStatus, TxJob
from txstratum.middleware import create_middleware_version_check

if TYPE_CHECKING:
    from txstratum.filters import TXFilter
    from txstratum.manager import TxMiningManager


# Default maximum txout script size (in bytes).
MAX_OUTPUT_SCRIPT_SIZE = 256

# Default maximum tx weight allowed to be mined in this server.
MAX_TX_WEIGHT: float = 35.0

# Maximum difference between tx timestamp and current time.
MAX_TIMESTAMP_DELTA: int = 300


# Tx timeout (seconds)
TX_TIMEOUT: float = 20.0

logger = get_logger()


class App:
    """API used to manage tx job."""

    def __init__(
        self,
        manager: "TxMiningManager",
        health_check: "HealthCheck",
        *,
        max_tx_weight: Optional[float] = None,
        max_timestamp_delta: Optional[int] = None,
        tx_timeout: Optional[float] = None,
        fix_invalid_timestamp: bool = False,
        max_output_script_size: Optional[int] = None,
        only_standard_script: bool = True,
        tx_filters: Optional[List["TXFilter"]] = None,
        min_wallet_desktop_version: Optional[str] = None,
        min_wallet_mobile_version: Optional[str] = None,
        min_wallet_headless_version: Optional[str] = None,
    ):
        """Init App."""
        super().__init__()
        self.log = logger.new()
        self.manager = manager
        self.health_check_manager = health_check
        self.max_tx_weight: float = max_tx_weight or MAX_TX_WEIGHT
        self.max_output_script_size = max_output_script_size or MAX_OUTPUT_SCRIPT_SIZE
        self.max_timestamp_delta: float = max_timestamp_delta or MAX_TIMESTAMP_DELTA
        self.tx_timeout: float = tx_timeout or TX_TIMEOUT
        self.only_standard_script: bool = only_standard_script
        self.tx_filters = tx_filters or []
        self.app = web.Application(
            middlewares=[
                create_middleware_version_check(
                    min_wallet_desktop_version,
                    min_wallet_mobile_version,
                    min_wallet_headless_version,
                )
            ]
        )
        self.app.router.add_get("/health-check", self.health_check)
        self.app.router.add_get("/health", self.health)
        self.app.router.add_get("/mining-status", self.mining_status)
        self.app.router.add_get("/job-status", self.job_status)
        self.app.router.add_post("/submit-job", self.submit_job)
        self.app.router.add_post("/cancel-job", self.cancel_job)

        # Signal handler to add CORS headers when preparing the response
        self.app.on_response_prepare.append(self.on_prepare)

        self.fix_invalid_timestamp: bool = fix_invalid_timestamp

    async def on_prepare(
        self, request: web.Request, response: web.StreamResponse
    ) -> None:
        """Set CORS headers for all responses on_prepare."""
        response.headers["Access-Control-Allow-Origin"] = "http://localhost:3000"
        response.headers["Access-Control-Allow-Methods"] = request.method
        response.headers[
            "Access-Control-Allow-Headers"
        ] = "x-prototype-version,x-requested-with,content-type"
        response.headers["Access-Control-Max-Age"] = "604800"

    async def health(self, request: web.Request) -> web.Response:
        """Return the health check status for the tx-mining-service."""
        health_check_result = await self.health_check_manager.get_health_check()
        http_status = health_check_result.get_http_status_code()

        return web.json_response(health_check_result.to_json(), status=http_status)

    # XXX: DEPRECATED, Use /health instead
    async def health_check(self, request: web.Request) -> web.Response:
        """Return that the service is running."""
        return web.json_response({"success": True})

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
            self.log.debug("cannot-decode-json")
            return web.json_response({"error": "cannot-decode-json"}, status=400)
        if not isinstance(data, dict):
            self.log.debug("json-must-be-an-object", data=data)
            return web.json_response({"error": "json-must-be-an-object"}, status=400)
        tx_hex = data.get("tx")
        if not tx_hex:
            self.log.debug("missing-tx", data=data)
            return web.json_response({"error": "missing-tx"}, status=400)
        try:
            tx_bytes = bytes.fromhex(tx_hex)
            tx = tx_or_block_from_bytes(tx_bytes)
        except (ValueError, TxValidationError, StructError):
            self.log.debug("invalid-tx(1)", data=data)
            return web.json_response({"error": "invalid-tx"}, status=400)

        if not isinstance(tx, (Transaction, TokenCreationTransaction)):
            self.log.debug("invalid-tx(2)", data=data)
            return web.json_response({"error": "invalid-tx"}, status=400)

        if tx.weight > self.max_tx_weight:
            self.log.debug("tx-weight-is-too-high", data=data)
            return web.json_response({"error": "tx-weight-is-too-high"}, status=400)

        if not tx.is_standard(self.max_output_script_size, self.only_standard_script):
            self.log.debug("non-standard-tx", data=data)
            return web.json_response({"error": "non-standard-tx"}, status=400)

        for tx_filter in self.tx_filters:
            if await tx_filter.check_tx(tx, data):
                return web.json_response({"error": "invalid-tx"}, status=400)

        now = txstratum.time.time()
        if abs(tx.timestamp - now) > self.max_timestamp_delta:
            if self.fix_invalid_timestamp:
                self.log.debug("fixing invalid timestamp...", data=data)
                tx.timestamp = int(now)
                tx_bytes = bytes(tx)
            else:
                self.log.debug("tx-timestamp-invalid", data=data)
                return web.json_response({"error": "tx-timestamp-invalid"}, status=400)

        if "timeout" not in data:
            timeout = self.tx_timeout
        else:
            try:
                timeout = min(self.tx_timeout, float(data["timeout"]))
            except ValueError:
                self.log.debug("invalid-timeout(1)", data=data)
                return web.json_response({"error": "invalid-timeout"}, status=400)

            if timeout <= 0:
                self.log.debug("invalid-timeout(2)", data=data)
                return web.json_response({"error": "invalid-timeout"}, status=400)

        add_parents = data.get("add_parents", False)
        propagate = data.get("propagate", False)

        job = TxJob(
            tx_bytes, add_parents=add_parents, propagate=propagate, timeout=timeout
        )
        try:
            self.manager.add_job(job)
        except JobAlreadyExists:
            self.log.debug("job-already-exists", data=data)
            running_job = self.manager.tx_jobs[job.uuid]
            return web.json_response(running_job.to_dict())
        except NewJobRefused:
            self.log.debug("new-job-refused", data=data)
            return web.json_response({"error": "new-job-refused"}, status=503)
        return web.json_response(job.to_dict())

    def _get_job(self, uuid_hex: Optional[str]) -> TxJob:
        """Return job from uuid_hex. It raises web exceptions for common issues."""
        if not uuid_hex:
            raise web.HTTPBadRequest(
                body=json.dumps({"error": "missing-job-id"}).encode("ascii"),
                content_type="application/json",
            )
        try:
            uuid = bytes.fromhex(uuid_hex)
        except ValueError:
            raise web.HTTPBadRequest(
                body=json.dumps({"error": "invalid-uuid"}).encode("ascii"),
                content_type="application/json",
            )
        job = self.manager.get_job(uuid)
        if job is None:
            raise web.HTTPNotFound(
                body=json.dumps({"error": "job-not-found"}).encode("ascii"),
                content_type="application/json",
            )
        return job

    async def job_status(self, request: web.Request) -> web.Response:
        """Get the status of a tx job.

        Method: GET
        Query:
        - job-id: str, job identifier
        """
        job = self._get_job(request.query.get("job-id"))
        return web.json_response(job.to_dict())

    async def cancel_job(self, request: web.Request) -> web.Response:
        """Cancel a tx job.

        Method: POST
        Query:
        - job-id: str, job identifier
        """
        job = self._get_job(request.query.get("job-id"))
        if job.status in JobStatus.get_after_mining_states():
            return web.json_response({"error": "job-has-already-finished"}, status=400)
        self.manager.cancel_job(job)
        return web.json_response({"cancelled": True, "job-id": job.uuid.hex()})

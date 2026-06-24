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
from txstratum.middleware import (
    create_error_middleware,
    create_middleware_version_check,
    get_real_ip,
)

if TYPE_CHECKING:
    from txstratum.filters import TXFilter
    from txstratum.protocols import MiningManager


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
        manager: "MiningManager",
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
                # Outermost: log any unhandled exception / 5xx with request context.
                create_error_middleware(self.log),
                create_middleware_version_check(
                    min_wallet_desktop_version,
                    min_wallet_mobile_version,
                    min_wallet_headless_version,
                ),
            ]
        )
        self.app.router.add_get("/health-check", self.health_check)
        self.app.router.add_get("/health", self.health)
        self.app.router.add_get("/mining-status", self.mining_status)
        self.app.router.add_get("/job-status", self.job_status)
        self.app.router.add_options("/submit-job", self.get_options_response)
        self.app.router.add_options("/cancel-job", self.get_options_response)
        self.app.router.add_post("/submit-job", self.submit_job)
        self.app.router.add_post("/cancel-job", self.cancel_job)

        # Signal handler to add CORS headers when preparing the response
        self.app.on_response_prepare.append(self.on_prepare)

        self.fix_invalid_timestamp: bool = fix_invalid_timestamp

    async def on_prepare(
        self, request: web.Request, response: web.StreamResponse
    ) -> None:
        """Set CORS headers for all responses on_prepare."""
        # We allow localhost:3000 because it's the host used by the desktop wallet
        # so devs can test running their own tx mining service with it
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

    async def get_options_response(self, request: web.Request) -> web.Response:
        """
        Return empty success response.

        This endpoint is required to make CORS work when using the desktop wallet with a local tx-mining-service.
        """
        return web.json_response("{}", status=200)

    async def submit_job(self, request: web.Request) -> web.Response:
        """Submit a new tx job to the manager.

        Method: POST
        Format: json
        Params:
        - tx: str, hex dump of the transaction
        - propagate: bool, propagate tx to Hathor’s full node after it is solved
        - add_parents: bool, add parents before resolving the tx
        """
        user_agent = request.headers.get("User-Agent")
        real_ip = get_real_ip(request)
        try:
            data = await request.json()
        except json.decoder.JSONDecodeError:
            self.log.warning(
                "submit-job-rejected",
                reason="cannot-decode-json",
                user_agent=user_agent,
                real_ip=real_ip,
            )
            return web.json_response({"error": "cannot-decode-json"}, status=400)
        if not isinstance(data, dict):
            self.log.warning(
                "submit-job-rejected",
                reason="json-must-be-an-object",
                user_agent=user_agent,
                real_ip=real_ip,
            )
            return web.json_response({"error": "json-must-be-an-object"}, status=400)
        tx_hex = data.get("tx")
        if not tx_hex:
            self.log.warning(
                "submit-job-rejected",
                reason="missing-tx",
                user_agent=user_agent,
                real_ip=real_ip,
            )
            return web.json_response({"error": "missing-tx"}, status=400)
        try:
            tx_bytes = bytes.fromhex(tx_hex)
            tx = tx_or_block_from_bytes(tx_bytes)
        except (ValueError, TxValidationError, StructError) as e:
            # Capture WHICH error and the offending hex so it can be re-parsed offline.
            self.log.warning(
                "submit-job-rejected",
                reason="invalid-tx",
                detail="parse-error",
                exc_type=type(e).__name__,
                exception=repr(e),
                tx_len=len(tx_hex),
                tx_hex=tx_hex,
                user_agent=user_agent,
                real_ip=real_ip,
            )
            return web.json_response({"error": "invalid-tx"}, status=400)

        if not isinstance(tx, (Transaction, TokenCreationTransaction)):
            self.log.warning(
                "submit-job-rejected",
                reason="invalid-tx",
                detail="not-a-transaction",
                tx_type=type(tx).__name__,
                tx_hex=tx_hex,
                user_agent=user_agent,
                real_ip=real_ip,
            )
            return web.json_response({"error": "invalid-tx"}, status=400)

        # Best-effort tx fingerprint for correlation; logging must never raise.
        try:
            tx_id = tx.calculate_hash().hex()
        except Exception:
            tx_id = None

        if tx.weight > self.max_tx_weight:
            self.log.warning(
                "submit-job-rejected",
                reason="tx-weight-is-too-high",
                tx_weight=tx.weight,
                max_tx_weight=self.max_tx_weight,
                tx_id=tx_id,
                tx_hex=tx_hex,
                user_agent=user_agent,
                real_ip=real_ip,
            )
            return web.json_response({"error": "tx-weight-is-too-high"}, status=400)

        if not tx.is_standard(self.max_output_script_size, self.only_standard_script):
            self.log.warning(
                "submit-job-rejected",
                reason="non-standard-tx",
                max_output_script_size=self.max_output_script_size,
                tx_id=tx_id,
                tx_hex=tx_hex,
                user_agent=user_agent,
                real_ip=real_ip,
            )
            return web.json_response({"error": "non-standard-tx"}, status=400)

        for tx_filter in self.tx_filters:
            if await tx_filter.check_tx(tx, data):
                self.log.warning(
                    "submit-job-rejected",
                    reason="tx-filter",
                    filter=type(tx_filter).__name__,
                    tx_id=tx_id,
                    tx_hex=tx_hex,
                    user_agent=user_agent,
                    real_ip=real_ip,
                )
                return web.json_response({"error": "invalid-tx"}, status=400)

        now = txstratum.time.time()
        if abs(tx.timestamp - now) > self.max_timestamp_delta:
            if self.fix_invalid_timestamp:
                self.log.debug("fixing invalid timestamp...", data=data)
                tx.timestamp = int(now)
                tx_bytes = bytes(tx)
            else:
                self.log.warning(
                    "submit-job-rejected",
                    reason="tx-timestamp-invalid",
                    tx_timestamp=tx.timestamp,
                    server_now=int(now),
                    delta=abs(tx.timestamp - now),
                    max_timestamp_delta=self.max_timestamp_delta,
                    tx_id=tx_id,
                    user_agent=user_agent,
                    real_ip=real_ip,
                )
                return web.json_response({"error": "tx-timestamp-invalid"}, status=400)

        if "timeout" not in data:
            timeout = self.tx_timeout
        else:
            try:
                timeout = min(self.tx_timeout, float(data["timeout"]))
            except ValueError:
                self.log.warning(
                    "submit-job-rejected",
                    reason="invalid-timeout",
                    requested_timeout=data.get("timeout"),
                    user_agent=user_agent,
                    real_ip=real_ip,
                )
                return web.json_response({"error": "invalid-timeout"}, status=400)

            if timeout <= 0:
                self.log.warning(
                    "submit-job-rejected",
                    reason="invalid-timeout",
                    requested_timeout=data.get("timeout"),
                    user_agent=user_agent,
                    real_ip=real_ip,
                )
                return web.json_response({"error": "invalid-timeout"}, status=400)

        add_parents = data.get("add_parents", False)
        propagate = data.get("propagate", False)

        job = TxJob(
            tx_bytes, add_parents=add_parents, propagate=propagate, timeout=timeout
        )
        try:
            self.manager.add_job(job)
        except JobAlreadyExists:
            self.log.info(
                "submit-job-duplicate",
                job_id=job.uuid.hex(),
                tx_id=tx_id,
                user_agent=user_agent,
                real_ip=real_ip,
            )
            running_job = self.manager.tx_jobs[job.uuid]
            return web.json_response(running_job.to_dict())
        except NewJobRefused:
            self.log.warning(
                "submit-job-refused",
                reason="new-job-refused",
                job_id=job.uuid.hex(),
                tx_id=tx_id,
                user_agent=user_agent,
                real_ip=real_ip,
            )
            return web.json_response({"error": "new-job-refused"}, status=503)
        # Keystone log: ties this job_id to the client so any later async failure
        # (add_parents / timeout / propagate) logged by the manager is attributable.
        self.log.info(
            "submit-job-accepted",
            job_id=job.uuid.hex(),
            tx_id=tx_id,
            tx_weight=tx.weight,
            add_parents=add_parents,
            propagate=propagate,
            timeout=timeout,
            user_agent=user_agent,
            real_ip=real_ip,
            tx_hex=tx_hex,
        )
        return web.json_response(job.to_dict())

    def _get_job(self, request: web.Request, uuid_hex: Optional[str]) -> TxJob:
        """Return job from uuid_hex. It raises web exceptions for common issues."""
        if not uuid_hex:
            self.log.info(
                "job-lookup-failed",
                reason="missing-job-id",
                endpoint=request.path,
                user_agent=request.headers.get("User-Agent"),
                real_ip=get_real_ip(request),
            )
            raise web.HTTPBadRequest(
                body=json.dumps({"error": "missing-job-id"}).encode("ascii"),
                content_type="application/json",
            )
        try:
            uuid = bytes.fromhex(uuid_hex)
        except ValueError:
            self.log.info(
                "job-lookup-failed",
                reason="invalid-uuid",
                job_id=uuid_hex,
                endpoint=request.path,
                user_agent=request.headers.get("User-Agent"),
                real_ip=get_real_ip(request),
            )
            raise web.HTTPBadRequest(
                body=json.dumps({"error": "invalid-uuid"}).encode("ascii"),
                content_type="application/json",
            )
        job = self.manager.get_job(uuid)
        if job is None:
            # A 404 here often means the job already timed out / was cleaned up:
            # correlate with the manager's "TxJob timeout" log for the same job_id.
            self.log.info(
                "job-lookup-failed",
                reason="job-not-found",
                job_id=uuid_hex,
                endpoint=request.path,
                user_agent=request.headers.get("User-Agent"),
                real_ip=get_real_ip(request),
            )
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
        job = self._get_job(request, request.query.get("job-id"))
        return web.json_response(job.to_dict())

    async def cancel_job(self, request: web.Request) -> web.Response:
        """Cancel a tx job.

        Method: POST
        Query:
        - job-id: str, job identifier
        """
        job = self._get_job(request, request.query.get("job-id"))
        if job.status in JobStatus.get_after_mining_states():
            return web.json_response({"error": "job-has-already-finished"}, status=400)
        self.manager.cancel_job(job)
        return web.json_response({"cancelled": True, "job-id": job.uuid.hex()})

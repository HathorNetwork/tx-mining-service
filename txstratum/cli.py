# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
import asyncio
import logging
import logging.config
import os
from argparse import ArgumentParser, Namespace
from asyncio.events import AbstractEventLoop
from typing import List, Optional

import structlog
from aiohttp import web
from hathorlib.client import HathorClient
from structlog import get_logger

from txstratum.api import App
from txstratum.filters import FileFilter, TOIFilter, TXFilter
from txstratum.manager import TxMiningManager
from txstratum.pubsub import PubSubManager
from txstratum.toi_client import TOIAsyncClient

logger = get_logger()


DEFAULT_LOGGING_CONFIG_FILE = "log.conf"


def create_parser() -> ArgumentParser:
    """Create a parser for the cmdline arguments."""
    import configargparse  # type: ignore

    parser: ArgumentParser = configargparse.ArgumentParser(auto_env_var_prefix="hathor_")
    parser.add_argument("--stratum-port", help="Port of Stratum server", type=int, default=8000)
    parser.add_argument("--api-port", help="Port of TxMining API server", type=int, default=8080)
    parser.add_argument("--max-tx-weight", help="Maximum allowed tx weight to be mined", type=float, default=None)
    parser.add_argument("--max-timestamp-delta", help="Maximum allowed tx timestamp delta", type=int, default=None)
    parser.add_argument("--tx-timeout", help="Tx mining timeout (seconds)", type=int, default=None)
    parser.add_argument("--fix-invalid-timestamp", action="store_true", help="Fix invalid timestamp to current time")
    parser.add_argument("--prometheus", help="Path to export metrics for Prometheus", type=str, default=None)
    parser.add_argument("--prometheus-port", help="Enables exporting metrics in a http port", type=int, default=None)
    parser.add_argument("--testnet", action="store_true", help="Use testnet config parameters")
    parser.add_argument("--address", help="Mining address for blocks", type=str, default=None)
    parser.add_argument("--allow-non-standard-script", action="store_true", help="Accept mining non-standard tx")
    parser.add_argument("--ban-tx-ids", help="File with list of banned tx ids", type=str, default=None)
    parser.add_argument("--ban-addrs", help="File with list of banned addresses", type=str, default=None)
    parser.add_argument("--toi-apikey", help="apikey for toi service", type=str, default=None)
    parser.add_argument("--toi-url", help="toi service url", type=str, default=None)
    parser.add_argument("--toi-fail-block", help="Block tx if toi fails", default=False, action="store_true")
    parser.add_argument(
        '--block-template-update-interval', help='Block template update interval', type=int, default=None)
    parser.add_argument('backend', help='Endpoint of the Hathor API (without version)', type=str)

    logs = parser.add_mutually_exclusive_group()
    logs.add_argument("--log-config", help="Config file for logging", default=DEFAULT_LOGGING_CONFIG_FILE)
    logs.add_argument("--json-logs", help="Enabled logging in json", default=False, action="store_true")
    return parser


class RunService:
    """This is the main class of the service. It starts everything up."""

    manager: TxMiningManager
    loop: AbstractEventLoop
    tx_filters: List[TXFilter]

    def __init__(self, args: Namespace) -> None:
        """Initialize the service."""
        self.args = args

        self.configure_logging(args)

        self.loop = asyncio.get_event_loop()

        self.pubsub = PubSubManager(self.loop)
        self.backend = HathorClient(args.backend)
        self.manager = TxMiningManager(
            backend=self.backend,
            pubsub=self.pubsub,
            address=args.address,
        )

    def configure_logging(self, args: Namespace) -> None:
        """Configure logging."""
        from txstratum.utils import start_logging

        start_logging()
        if args.json_logs:
            logging.basicConfig(level=logging.INFO, format="%(message)s")
            from structlog.stdlib import LoggerFactory

            structlog.configure(
                logger_factory=LoggerFactory(),
                processors=[
                    structlog.stdlib.filter_by_level,
                    structlog.stdlib.add_logger_name,
                    structlog.processors.add_log_level,
                    structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
                    structlog.processors.format_exc_info,
                    structlog.processors.JSONRenderer(),
                ],
            )
            logger.info("tx-mining-service", backend=args.backend)
            logger.info("Logging with json format...")
        elif os.path.exists(args.log_config):
            logging.config.fileConfig(args.log_config)
            from structlog.stdlib import LoggerFactory

            structlog.configure(logger_factory=LoggerFactory())
            logger.info("tx-mining-service", backend=args.backend)
            logger.info("Configuring log...", log_config=args.log_config)
        else:
            logger.info("tx-mining-service", backend=args.backend)
            logger.info("Log config file not found; using default configuration.", log_config=args.log_config)

    def execute(self) -> None:
        """Run the service according to the args."""
        if self.args.block_template_update_interval:
            self.manager.block_template_update_interval = self.args.block_template_update_interval

        self.loop.run_until_complete(self.backend.start())
        self.loop.run_until_complete(self.manager.start())
        self.server = self.loop.run_until_complete(
            self.loop.create_server(self.manager, "0.0.0.0", self.args.stratum_port)
        )
        self.tx_filters: List[TXFilter] = []
        toiclient: Optional[TOIAsyncClient] = None

        if self.args.prometheus:
            from txstratum.prometheus import PrometheusExporter

            metrics = PrometheusExporter(self.manager, self.pubsub, self.args.prometheus)
            metrics.start()

        if self.args.prometheus_port:
            from txstratum.prometheus import HttpPrometheusExporter

            http_metrics = HttpPrometheusExporter(self.manager, self.pubsub, self.args.prometheus_port)
            http_metrics.start()
            logger.info("Prometheus metrics server running at 0.0.0.0:{}...".format(self.args.prometheus_port))

        if self.args.ban_addrs or self.args.ban_tx_ids:
            self.tx_filters.append(FileFilter.load_from_files(self.args.ban_tx_ids, self.args.ban_addrs))

        if self.args.toi_url or self.args.toi_apikey:
            if not (self.args.toi_url and self.args.toi_apikey):
                raise ValueError("Should pass both toi_url and toi_apikey")
            toiclient = TOIAsyncClient(self.args.toi_url, self.args.toi_apikey)
            self.tx_filters.append(TOIFilter(toiclient, block=self.args.toi_fail_block))

        api_app = App(
            self.manager, max_tx_weight=self.args.max_tx_weight, max_timestamp_delta=self.args.max_timestamp_delta,
            tx_timeout=self.args.tx_timeout, fix_invalid_timestamp=self.args.fix_invalid_timestamp,
            only_standard_script=not self.args.allow_non_standard_script, tx_filters=self.tx_filters
        )

        logger.info(
            "API Configuration",
            max_tx_weight=api_app.max_tx_weight,
            tx_timeout=api_app.tx_timeout,
            max_timestamp_delta=api_app.max_timestamp_delta,
            fix_invalid_timestamp=api_app.fix_invalid_timestamp,
            only_standard_script=api_app.only_standard_script,
            tx_filters=self.tx_filters,
        )

        web_runner = web.AppRunner(api_app.app, logger=logger)
        self.loop.run_until_complete(web_runner.setup())
        site = web.TCPSite(web_runner, "0.0.0.0", self.args.api_port)
        self.loop.run_until_complete(site.start())

        self.register_signal_handlers()

        logger.info("Stratum Server running at 0.0.0.0:{}...".format(self.args.stratum_port))
        logger.info("TxMining API running at 0.0.0.0:{}...".format(self.args.api_port))
        if self.args.testnet:
            logger.info("Running with testnet config file")
        self.loop.run_forever()

    def handle_shutdown_signal(self, signal: str) -> None:
        """Handle shutdown signals."""
        logger.info(f"{signal} received.")

        self.loop.create_task(self._shutdown())

    def register_signal_handlers(self) -> None:
        """Register signal handlers."""
        import signal

        logger.info("Registering signal handlers...")

        sigterm = getattr(signal, "SIGTERM", None)
        if sigterm is not None:
            self.loop.add_signal_handler(sigterm, lambda: self.handle_shutdown_signal("SIGTERM"))

        sigint = getattr(signal, "SIGINT", None)
        if sigint is not None:
            self.loop.add_signal_handler(sigint, lambda: self.handle_shutdown_signal("SIGINT"))

    async def _graceful_shutdown(self) -> None:
        """Gracefully shutdown the service."""
        logger.info("Gracefully shutting down...")

        self.manager.refuse_new_jobs = True

        while len(self.manager.tx_queue) > 0:
            logger.info("Waiting for pending txs to finish...", txs_left=len(self.manager.tx_queue))
            await asyncio.sleep(2)

        self.manager.shutdown()

    async def _shutdown(self) -> None:
        """Shutdown the service."""
        logger.info("Shutting down...")
        await self._graceful_shutdown()

        for tx_filter in self.tx_filters:
            await tx_filter.close()

        self.server.close()

        await self.server.wait_closed()
        await self.backend.stop()
        await self.manager.stop()

        self.loop.stop()


def main() -> None:
    """Run the service using the cmdline."""
    parser = create_parser()
    args = parser.parse_args()

    if args.testnet:
        if not os.environ.get("TXMINING_CONFIG_FILE"):
            os.environ["TXMINING_CONFIG_FILE"] = "hathorlib.conf.testnet"

    RunService(args).execute()

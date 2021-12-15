# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
import asyncio
import logging
import logging.config
import os
from argparse import ArgumentParser, Namespace
from typing import List, Optional

import structlog
from aiohttp import web
from structlog import get_logger

logger = get_logger()


DEFAULT_LOGGING_CONFIG_FILE = "log.conf"


def create_parser() -> ArgumentParser:
    """Create a parser for the cmdline arguments."""
    import configargparse  # type: ignore
    parser: ArgumentParser = configargparse.ArgumentParser(auto_env_var_prefix='hathor_')
    parser.add_argument('--stratum-port', help='Port of Stratum server', type=int, default=8000)
    parser.add_argument('--api-port', help='Port of TxMining API server', type=int, default=8080)
    parser.add_argument('--log-config', help='Config file for logging', default=None)
    parser.add_argument('--json-logs', help='Enabled logging in json', default=False, action='store_true')
    parser.add_argument('--max-tx-weight', help='Maximum allowed tx weight to be mined', type=float, default=None)
    parser.add_argument('--max-timestamp-delta', help='Maximum allowed tx timestamp delta', type=int, default=None)
    parser.add_argument('--tx-timeout', help='Tx mining timeout (seconds)', type=int, default=None)
    parser.add_argument('--fix-invalid-timestamp', action='store_true', help='Fix invalid timestamp to current time')
    parser.add_argument('--prometheus', help='Path to export metrics for Prometheus', type=str, default=None)
    parser.add_argument('--testnet', action='store_true', help='Use testnet config parameters')
    parser.add_argument('--address', help='Mining address for blocks', type=str, default=None)
    parser.add_argument('--allow-non-standard-script', action='store_true', help='Accept mining non-standard tx')
    parser.add_argument('--ban-tx-ids', help='File with list of banned tx ids', type=str, default=None)
    parser.add_argument('--ban-addrs', help='File with list of banned addresses', type=str, default=None)
    parser.add_argument('--toi-apikey', help='apikey for toi service', type=str, default=None)
    parser.add_argument('--toi-url', help='toi service url', type=str, default=None)
    parser.add_argument('--toi-fail-block', help='Block tx if toi fails', default=False, action='store_true')
    parser.add_argument('backend', help='Endpoint of the Hathor API (without version)', type=str)
    return parser


def execute(args: Namespace) -> None:
    """Run the service according to the args."""
    from hathorlib.client import HathorClient

    from txstratum.api import App
    from txstratum.filters import FileFilter, TOIFilter, TXFilter
    from txstratum.manager import TxMiningManager
    from txstratum.toi_client import TOIAsyncClient
    from txstratum.utils import start_logging

    # Configure log.
    log_config_file = args.log_config or DEFAULT_LOGGING_CONFIG_FILE

    start_logging()
    if args.json_logs:
        if args.log_config:
            logger.error('--json-logs and --log-config are mutually exclusive')
            exit(1)

        logging.basicConfig(level=logging.INFO, format='%(message)s')
        from structlog.stdlib import LoggerFactory
        structlog.configure(
            logger_factory=LoggerFactory(),
            processors=[
                structlog.stdlib.filter_by_level,
                structlog.stdlib.add_logger_name,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer()
            ])
        logger.info('tx-mining-service', backend=args.backend)
        logger.info('Logging with json format...')
    elif os.path.exists(log_config_file):
        logging.config.fileConfig(log_config_file)
        from structlog.stdlib import LoggerFactory
        structlog.configure(logger_factory=LoggerFactory())
        logger.info('tx-mining-service', backend=args.backend)
        logger.info('Configuring log...', log_config=log_config_file)
    else:
        logger.info('tx-mining-service', backend=args.backend)
        logger.info('Log config file not found; using default configuration.', log_config=log_config_file)

    # Set up all parts.
    loop = asyncio.get_event_loop()

    backend = HathorClient(args.backend)
    manager = TxMiningManager(
        backend=backend,
        address=args.address,
    )
    loop.run_until_complete(backend.start())
    loop.run_until_complete(manager.start())
    server = loop.run_until_complete(loop.create_server(manager, '0.0.0.0', args.stratum_port))
    tx_filters: List[TXFilter] = []
    toiclient: Optional[TOIAsyncClient] = None

    if args.prometheus:
        from txstratum.prometheus import PrometheusExporter
        metrics = PrometheusExporter(manager, args.prometheus)
        metrics.start()

    if args.ban_addrs or args.ban_tx_ids:
        tx_filters.append(FileFilter.load_from_files(args.ban_tx_ids, args.ban_addrs))

    if args.toi_url or args.toi_apikey:
        if not (args.toi_url and args.toi_apikey):
            raise ValueError("Should pass both toi_url and toi_apikey")
        toiclient = TOIAsyncClient(args.toi_url, args.toi_apikey)
        tx_filters.append(TOIFilter(toiclient, block=args.toi_fail_block))

    api_app = App(manager, max_tx_weight=args.max_tx_weight, max_timestamp_delta=args.max_timestamp_delta,
                  tx_timeout=args.tx_timeout, fix_invalid_timestamp=args.fix_invalid_timestamp,
                  only_standard_script=not args.allow_non_standard_script, tx_filters=tx_filters)
    logger.info('API Configuration', max_tx_weight=api_app.max_tx_weight, tx_timeout=api_app.tx_timeout,
                max_timestamp_delta=api_app.max_timestamp_delta, fix_invalid_timestamp=api_app.fix_invalid_timestamp,
                only_standard_script=api_app.only_standard_script, tx_filters=tx_filters)

    web_runner = web.AppRunner(api_app.app)
    loop.run_until_complete(web_runner.setup())
    site = web.TCPSite(web_runner, '0.0.0.0', args.api_port)
    loop.run_until_complete(site.start())

    try:
        logger.info('Stratum Server running at 0.0.0.0:{}...'.format(args.stratum_port))
        logger.info('TxMining API running at 0.0.0.0:{}...'.format(args.api_port))
        if args.testnet:
            logger.info('Running with testnet config file')
        loop.run_forever()
    except KeyboardInterrupt:
        logger.info('Stopping...')

    for tx_filter in tx_filters:
        loop.run_until_complete(tx_filter.close())
    server.close()
    loop.run_until_complete(server.wait_closed())
    loop.run_until_complete(backend.stop())
    loop.close()


def main() -> None:
    """Run the service using the cmdline."""
    parser = create_parser()
    args = parser.parse_args()

    if args.testnet:
        if not os.environ.get('TXMINING_CONFIG_FILE'):
            os.environ['TXMINING_CONFIG_FILE'] = 'hathorlib.conf.testnet'

    execute(args)

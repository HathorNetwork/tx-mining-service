# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
import asyncio
import logging
import logging.config
import os
from argparse import ArgumentParser, Namespace

import structlog  # type: ignore
from aiohttp import web
from structlog import get_logger

logger = get_logger()


def create_parser() -> ArgumentParser:
    """Create a parser for the cmdline arguments."""
    import configargparse  # type: ignore
    parser: ArgumentParser = configargparse.ArgumentParser(auto_env_var_prefix='hathor_')
    parser.add_argument('--stratum-port', help='Port of Stratum server', type=int, default=8000)
    parser.add_argument('--api-port', help='Port of TxMining API server', type=int, default=8080)
    parser.add_argument('--log-config', help='Config file for logging', default='log.conf')
    parser.add_argument('--max-tx-weight', help='Maximum allowed tx weight to be mined', type=int, default=None)
    parser.add_argument('--tx-timeout', help='Tx mining timeout (seconds)', type=int, default=None)
    parser.add_argument('--prometheus', help='Path to export metrics for Prometheus', type=str, default=None)
    parser.add_argument('--testnet', action='store_true', help='Use testnet config parameters')
    parser.add_argument('backend', help='Endpoint of the Hathor API (without version)', type=str)
    return parser


def execute(args: Namespace) -> None:
    """Run the service according to the args."""
    from txstratum.api import App
    from txstratum.commons.client import HathorClient
    from txstratum.manager import TxMiningManager

    # Configure log.
    if os.path.exists(args.log_config):
        logging.config.fileConfig(args.log_config)
        from structlog.stdlib import LoggerFactory  # type: ignore
        structlog.configure(logger_factory=LoggerFactory())
        logger.info('tx-mining-service', backend=args.backend)
        logger.info('Configuring log...', log_config=args.log_config)
    else:
        logger.info('tx-mining-service', backend=args.backend)
        logger.info('Log config file not found; using default configuration.', log_config=args.log_config)

    # Set up all parts.
    loop = asyncio.get_event_loop()

    backend = HathorClient(args.backend)
    manager = TxMiningManager(
        backend=backend
    )
    loop.run_until_complete(backend.start())
    loop.run_until_complete(manager.start())
    server = loop.run_until_complete(loop.create_server(manager, '0.0.0.0', args.stratum_port))

    if args.prometheus:
        from txstratum.prometheus import PrometheusExporter
        metrics = PrometheusExporter(manager, args.prometheus)
        metrics.start()

    api_app = App(manager, max_tx_weight=args.max_tx_weight, tx_timeout=args.tx_timeout)
    logger.info('API Configuration', max_tx_weight=api_app.max_tx_weight, tx_timeout=api_app.tx_timeout)
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
            os.environ['TXMINING_CONFIG_FILE'] = 'txstratum.commons.conf.testnet'

    execute(args)

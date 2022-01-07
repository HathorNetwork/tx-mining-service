import asyncio
import os
from typing import TYPE_CHECKING, Dict, NamedTuple

from prometheus_client import CollectorRegistry, Gauge, start_http_server, write_to_textfile  # type: ignore

from txstratum.utils import Periodic

if TYPE_CHECKING:
    from txstratum.manager import TxMiningManager


# Define prometheus metrics.
# Key: str = name of the metric
# Value: Tuple[str, Callable[[TxMiningManager], Any]] = method that collects the metric
METRIC_INFO: Dict[str, str] = {
    'miners_count': 'Number of connected miners',
    'total_hashrate_ghs': 'Hashrate (Gh/s)',
    'txs_solved': 'Number of solved transactions',
    'txs_timeout': 'Number of timeouts when solving transactions',
    'blocks_found': 'Number of blocks found',
    'uptime': 'Service uptime',
    'tx_queue': 'Number of transactions in the queue',
    'block_template_error': 'Number of errors updating block template',
}


class MetricData(NamedTuple):
    """Store collected data."""

    miners_count: int
    total_hashrate_ghs: float
    txs_solved: int
    txs_timeout: int
    blocks_found: int
    uptime: float
    tx_queue: int
    block_template_error: int


def collect_metrics(manager: 'TxMiningManager') -> MetricData:
    """Collect data from TxMiningManager."""
    return MetricData(
        miners_count=len(manager.miners),
        total_hashrate_ghs=manager.get_total_hashrate_ghs(),
        txs_solved=manager.txs_solved,
        txs_timeout=manager.txs_timeout,
        blocks_found=manager.blocks_found,
        uptime=manager.uptime,
        tx_queue=len(manager.tx_queue),
        block_template_error=manager.block_template_error,
    )


class BasePrometheusExporter:
    """Base class for prometheus exporters."""

    def __init__(self, manager: 'TxMiningManager'):
        """Init BasePrometheusExporter.

        :param manager: Manager where the metrics will be collected from
        """
        self.manager = manager

        # Stores all Gauge objects for each metric (key is the metric name)
        self.metric_gauges: Dict[str, Gauge] = {}

        # Setup initial prometheus lib objects for each metric
        self._initial_setup()

        # Interval in which the write data method will be called (in seconds)
        self.call_interval: int = 5

        # Periodic task to update metrics
        self.update_metrics_task: Periodic = Periodic(self.update_metrics, self.call_interval)

    def _initial_setup(self) -> None:
        """Start a collector registry to send data to node exporter."""
        self.registry = CollectorRegistry()

        for name, comment in METRIC_INFO.items():
            self.metric_gauges[name] = Gauge(name, comment, registry=self.registry)

    async def update_metrics(self) -> None:
        """Update metric_gauges dict with new data from metrics."""
        data = collect_metrics(self.manager)
        for metric_name in METRIC_INFO.keys():
            self.metric_gauges[metric_name].set(getattr(data, metric_name))

    def start(self) -> None:
        """Start exporter."""
        asyncio.ensure_future(self.update_metrics_task.start())

    def stop(self) -> None:
        """Stop exporter."""
        asyncio.ensure_future(self.update_metrics_task.stop())


class PrometheusExporter(BasePrometheusExporter):
    """Class that sends hathor metrics to a node exporter that will be read by Prometheus."""

    def __init__(self, manager: 'TxMiningManager', path: str, filename: str = 'tx-mining-service.prom'):
        """Init PrometheusExporter.

        :param manager: Manager where the metrics will be collected from
        :param path: Path to save the prometheus file
        :param filename: Name of the prometheus file (must end in .prom)
        """
        super().__init__(manager)

        # Create full directory, if does not exist
        os.makedirs(path, exist_ok=True)

        # Full filepath with filename
        self.filepath: str = os.path.join(path, filename)

    async def update_metrics(self) -> None:
        """Update metric_gauges dict with new data from metrics."""
        await super().update_metrics()

        write_to_textfile(self.filepath, self.registry)


class HttpPrometheusExporter(BasePrometheusExporter):
    """Class that exposes metrics in a http endpoint."""

    def __init__(self, manager: 'TxMiningManager', port: int):
        """Init HttpPrometheusExporter.

        :param manager: Manager where the metrics will be collected from
        :param port: Port to expose the metrics
        """
        super().__init__(manager)

        self.port = port

    def start(self) -> None:
        """Start exporter."""
        super().start()

        start_http_server(self.port, registry=self.registry)

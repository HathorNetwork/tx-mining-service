import asyncio
import os
from typing import TYPE_CHECKING, Dict, NamedTuple, Union, cast

from prometheus_client import (  # type: ignore[import]
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    start_http_server,
    write_to_textfile,
)

from txstratum.jobs import TxJob
from txstratum.protocol import StratumProtocol
from txstratum.pubsub import PubSubManager, TxMiningEvents
from txstratum.utils import Periodic

if TYPE_CHECKING:
    from txstratum.manager import TxMiningManager


# Define prometheus metrics.
# Key: str = name of the metric
# Value: Tuple[str, Callable[[TxMiningManager], Any]] = method that collects the metric
METRIC_INFO: Dict[str, str] = {
    "miners_count": "Number of connected miners",
    "total_hashrate_ghs": "Hashrate (Gh/s)",
    "txs_timeout": "Number of timeouts when solving transactions",
    "blocks_found": "Number of blocks found",
    "uptime": "Service uptime",
    "tx_queue": "Number of transactions in the queue",
    "block_template_error": "Number of errors updating block template",
}

# Metrics that we update through pubsub events
METRICS_PUBSUB = {
    "txs_jobs_received": Counter(
        "txs_jobs_received",
        "Number of transactions received by the API",
    ),
    "txs_solved": Counter(
        "txs_solved",
        "Number of solved transactions",
        labelnames=["miner_type", "miner_address"],
    ),
    "txs_solved_weight": Histogram(
        "txs_solved_weight",
        "Txs solved histogram by tx weight",
        buckets=(17, 18, 19, 20, 21, 22, 23, 25, float("inf")),
        labelnames=["miner_type"],
    ),
    "txs_timeout_weight": Histogram(
        "txs_timeout_weight",
        "Txs timeouts histogram by tx weight",
        buckets=(17, 18, 19, 20, 21, 22, 23, 25, float("inf")),
    ),
    "txs_mining_time": Histogram(
        "txs_mining_time",
        "Txs mining time histogram",
        buckets=(1, 3, 5, 7, 10, 13, 16, 20, float("inf")),
        labelnames=["miner_type"],
    ),
    "txs_waiting_time": Histogram(
        "txs_waiting_time",
        "Txs queue waiting time histogram",
        buckets=(0.1, 0.25, 0.5, 1, 2, 3, 5, 10, float("inf")),
    ),
    "miner_completed_jobs": Counter(
        "miner_completed_jobs",
        "Number of completed jobs by miner",
        labelnames=["miner_type", "miner_address"],
    ),
    "miner_up": Gauge(
        "miner_up",
        "Indicates that a miner is up",
        labelnames=["miner_address"],
    ),
}


class MetricData(NamedTuple):
    """Store collected data."""

    miners_count: int
    total_hashrate_ghs: float
    txs_timeout: int
    blocks_found: int
    uptime: float
    tx_queue: int
    block_template_error: int


def collect_metrics(manager: "TxMiningManager") -> MetricData:
    """Collect data from TxMiningManager."""
    return MetricData(
        miners_count=manager.get_miners_count(),
        total_hashrate_ghs=manager.get_total_hashrate_ghs(),
        txs_timeout=manager.txs_timeout,
        blocks_found=manager.blocks_found,
        uptime=manager.uptime,
        tx_queue=len(manager.tx_queue),
        block_template_error=manager.block_template_error,
    )


class BasePrometheusExporter:
    """Base class for prometheus exporters."""

    def __init__(self, manager: "TxMiningManager", pubsub: "PubSubManager") -> None:
        """Init BasePrometheusExporter.

        :param manager: Manager where the metrics will be collected from
        :param pubsub: PubSubManager to receive events that will trigger the update of metrics
        """
        self.manager = manager
        self.pubsub = pubsub

        # Stores all Gauge objects for each metric (key is the metric name)
        self.metric_gauges: Dict[str, Gauge] = {}

        # Setup initial prometheus lib objects for each metric
        self._initial_setup()

        # Interval in which the write data method will be called (in seconds)
        self.call_interval: int = 5

        # Periodic task to update metrics
        self.update_metrics_task: Periodic = Periodic(
            self.update_metrics, self.call_interval
        )

    def _initial_setup(self) -> None:
        """Start a collector registry to send data to node exporter."""
        self.registry = CollectorRegistry()

        for name, comment in METRIC_INFO.items():
            self.metric_gauges[name] = Gauge(name, comment, registry=self.registry)

        for _, metric in METRICS_PUBSUB.items():
            self.registry.register(metric)

        self.pubsub.subscribe(TxMiningEvents.MANAGER_TX_SOLVED, self._handle_tx_solved)
        self.pubsub.subscribe(
            TxMiningEvents.MANAGER_TX_TIMEOUT, self._handle_tx_timeout
        )
        self.pubsub.subscribe(
            TxMiningEvents.MANAGER_NEW_TX_JOB, self._handle_new_tx_job
        )
        self.pubsub.subscribe(
            TxMiningEvents.PROTOCOL_JOB_COMPLETED, self._handle_protocol_job_completed
        )
        self.pubsub.subscribe(
            TxMiningEvents.PROTOCOL_MINER_SUBSCRIBED,
            self._handle_protocol_miner_subscribed,
        )
        self.pubsub.subscribe(
            TxMiningEvents.PROTOCOL_MINER_DISCONNECTED,
            self._handle_protocol_miner_disconnected,
        )

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

    async def _handle_tx_solved(
        self, obj: Dict[str, Union[TxJob, StratumProtocol]]
    ) -> None:
        tx_job = cast(TxJob, obj["tx_job"])
        protocol = cast(StratumProtocol, obj["protocol"])

        METRICS_PUBSUB["txs_solved"].labels(
            miner_type=protocol.miner_type, miner_address=protocol.miner_address_str
        ).inc()

        METRICS_PUBSUB["txs_solved_weight"].labels(
            miner_type=protocol.miner_type,
        ).observe(tx_job.get_weight())

        METRICS_PUBSUB["txs_mining_time"].labels(
            miner_type=protocol.miner_type,
        ).observe(tx_job.get_mining_time())

        METRICS_PUBSUB["txs_waiting_time"].observe(tx_job.get_waiting_time())

    async def _handle_tx_timeout(self, obj: TxJob) -> None:
        tx_job = obj

        METRICS_PUBSUB["txs_timeout_weight"].observe(tx_job.get_weight())

    async def _handle_new_tx_job(self, obj: TxJob) -> None:
        METRICS_PUBSUB["txs_jobs_received"].inc()

    async def _handle_protocol_job_completed(self, protocol: StratumProtocol) -> None:
        METRICS_PUBSUB["miner_completed_jobs"].labels(
            miner_type=protocol.miner_type, miner_address=protocol.miner_address_str
        ).inc()

    async def _handle_protocol_miner_subscribed(
        self, protocol: StratumProtocol
    ) -> None:
        METRICS_PUBSUB["miner_up"].labels(miner_address=protocol.miner_address_str).set(
            1
        )

    async def _handle_protocol_miner_disconnected(
        self, protocol: StratumProtocol
    ) -> None:
        METRICS_PUBSUB["miner_up"].labels(miner_address=protocol.miner_address_str).set(
            0
        )


class PrometheusExporter(BasePrometheusExporter):
    """Class that sends hathor metrics to a node exporter that will be read by Prometheus."""

    def __init__(
        self,
        manager: "TxMiningManager",
        pubsub: "PubSubManager",
        path: str,
        filename: str = "tx-mining-service.prom",
    ):
        """Init PrometheusExporter.

        :param manager: Manager where the metrics will be collected from
        :param path: Path to save the prometheus file
        :param filename: Name of the prometheus file (must end in .prom)
        """
        super().__init__(manager, pubsub)

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

    def __init__(self, manager: "TxMiningManager", pubsub: "PubSubManager", port: int):
        """Init HttpPrometheusExporter.

        :param manager: Manager where the metrics will be collected from
        :param port: Port to expose the metrics
        """
        super().__init__(manager, pubsub)

        self.port = port

    def start(self) -> None:
        """Start exporter."""
        super().start()

        start_http_server(self.port, registry=self.registry)

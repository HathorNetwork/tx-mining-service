"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""
import asyncio
import os
import shutil
import tempfile

import asynctest  # type: ignore[import]

from txstratum.jobs import TxJob
from txstratum.prometheus import METRIC_INFO, MetricData, PrometheusExporter
from txstratum.protocol import StratumProtocol
from txstratum.pubsub import PubSubManager, TxMiningEvents

TX1_DATA = bytes.fromhex(
    "0001000102000000000000089c0d40a9b1edfb499bc624833fde87ae459d495000393f4aaa00006"
    "a473045022100c407d5e8f411f9ae582ebd7acbfcb6ea6170332709fb69acaa34c1b426f1d8f502"
    "2003847963768eca9bcdf46e758319fb2699fd28ab657d00f54bef46c37a90405e2103755f2920f"
    "f7dc32dc5414cea1cf9e078347f40894caf0c03637d083dbb261c5c000003e800001976a914a04c"
    "9e2a0291f53c618fdad2ecb37748efb0eeeb88ac0000151800001976a914545f1156a3b00df622b"
    "1d92968c21b962e9d7aa588ac4032a8228c4020c35ed18547020000000047c9881d2bf348d5ffd6"
    "ce8398d6bc5d17b3bea75a53c15b7480be950000006ed5794bf69ebe7d7d75e7a0024d98acb85cb"
    "9c101b59b8b6073e8667c84e2ee77"
)


class TxMiningManagerMock:
    """Mock TxMiningManager."""

    def __init__(self):
        self.miners = ["miner1", "miner2"]
        self.block_template_error = 1
        self.txs_timeout = 2
        self.blocks_found = 3
        self.txs_solved = 4
        self.uptime = 1234
        self.tx_queue = ["tx1", "tx2"]

    def get_total_hashrate_ghs(self):
        return 1.23


class ManagerTestCase(asynctest.TestCase):  # type: ignore[misc]
    def setUp(self):
        self.pubsub = PubSubManager(self.loop)
        self.manager = TxMiningManagerMock()
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        # Remove temp directory.
        shutil.rmtree(self.tmpdir)

    async def _run_all_pending_events(self):
        """Run all pending events."""
        # pending = asyncio.all_tasks(self.loop)
        # self.loop.run_until_complete(asyncio.gather(*pending))
        async def _fn():
            self.ran_all = True

        self.loop.create_task(_fn())

        while getattr(self, "ran_all", False) is False:
            await asyncio.sleep(0.1)

        self.ran_all = False

    def test_collect_metrics(self):
        description_keys = set(METRIC_INFO.keys())
        metric_keys = set(MetricData._fields)
        self.assertEqual(description_keys, metric_keys)

    async def test_update_metrics(self):
        prometheus = PrometheusExporter(self.manager, self.pubsub, self.tmpdir)
        await prometheus.update_metrics()
        self.assertTrue(os.path.exists(prometheus.filepath))

        # Emit some events to update metrics
        txJob = TxJob(data=TX1_DATA)
        txJob.created_at = 80
        txJob.started_at = 90
        txJob.submitted_at = 100

        protocol = StratumProtocol(self.manager)
        protocol.miner_address_str = "abc123"
        protocol.miner_version = "cpuminer/1.0"

        self.pubsub.emit(
            TxMiningEvents.MANAGER_TX_SOLVED, {"tx_job": txJob, "protocol": protocol}
        )
        await self._run_all_pending_events()

        await prometheus.update_metrics()

        # Check the metrics match the expected values
        with open(
            "tests/fixtures/expected_prometheus_metrics.prom", "r"
        ) as fp_expected:
            content_expected = fp_expected.read()

            # Removes lines with timestamp
            content_expected = list(
                filter(lambda x: x.find("_created") < 0, content_expected.splitlines())
            )

        with open(prometheus.filepath, "r") as fp:
            content = fp.read()

            # Removes lines with timestamp
            content = list(
                filter(lambda x: x.find("_created") < 0, content.splitlines())
            )

        self.assertEqual(content_expected, content)

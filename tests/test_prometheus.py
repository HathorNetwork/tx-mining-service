"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from tests.utils import async_test
from txstratum.prometheus import METRIC_INFO, MetricData, PrometheusExporter


class TxMiningManagerMock():
    """Mock TxMiningManager."""

    def __init__(self):
        self.miners = ['miner1', 'miner2']
        self.block_template_error = 1
        self.txs_timeout = 2
        self.blocks_found = 3
        self.txs_solved = 4
        self.uptime = 1234
        self.tx_queue = ['tx1', 'tx2']

    def get_total_hashrate_ghs(self):
        return 1.23


class ManagerTestCase(unittest.TestCase):
    def setUp(self):
        self.manager = TxMiningManagerMock()
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        # Remove temp directory.
        shutil.rmtree(self.tmpdir)

    def test_collect_metrics(self):
        description_keys = set(METRIC_INFO.keys())
        metric_keys = set(MetricData._fields)
        self.assertEqual(description_keys, metric_keys)

    @async_test
    async def test_update_metrics(self):
        prometheus = PrometheusExporter(self.manager, MagicMock(), self.tmpdir)
        await prometheus.update_metrics()
        self.assertTrue(os.path.exists(prometheus.filepath))

        with open("tests/fixtures/expected_prometheus_metrics.prom", "r") as fp_expected:
            content_expected = fp_expected.read()

            "ef".find

            # Removes lines with timestamp
            content_expected = list(filter(lambda x: x.find("_created") < 0, content_expected.splitlines()))

        with open(prometheus.filepath, 'r') as fp:
            content = fp.read()

            print(content)

            # Removes lines with timestamp
            content = list(filter(lambda x: x.find("_created") < 0, content.splitlines()))

        self.assertEqual(content_expected, content)

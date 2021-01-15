"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""
import os
import shutil
import tempfile
import unittest

from txstratum.manager import TxMiningManager
from txstratum.prometheus import METRIC_INFO, MetricData, PrometheusExporter


class ManagerTestCase(unittest.TestCase):
    def setUp(self):
        self.manager = TxMiningManager(backend=None, address=None)
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        # Remove temp directory.
        shutil.rmtree(self.tmpdir)

    def test_collect_metrics(self):
        description_keys = set(METRIC_INFO.keys())
        metric_keys = set(MetricData._fields)
        self.assertEqual(description_keys, metric_keys)

    def test_update_metrics(self):
        prometheus = PrometheusExporter(self.manager, self.tmpdir)
        prometheus.update_metrics()
        self.assertTrue(os.path.exists(prometheus.filepath))

        # Check if all metrics are in the file.
        with open(prometheus.filepath, 'r') as fp:
            keys = set()
            for line in fp.readlines():
                if not line:
                    # Skip empty lines.
                    continue
                if line[0] == '#':
                    # Skip comments.
                    continue
                metric_name, _, value = line.partition(' ')
                self.assertNotIn(metric_name, keys)
                keys.add(metric_name)
        expected_keys = set(prometheus.metric_gauges.keys())
        self.assertEqual(keys, expected_keys)

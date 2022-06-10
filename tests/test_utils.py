"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""
import unittest

from txstratum.exceptions import InvalidVersionFormat
from txstratum.utils import is_version_gte


class UtilsTestCase(unittest.TestCase):
    def test_versions_check(self):
        self.assertTrue(is_version_gte("1.2.3", "1.2.3"))
        self.assertFalse(is_version_gte("1.2.2", "1.2.3"))
        self.assertTrue(is_version_gte("1.2.4", "1.2.3"))

        self.assertTrue(is_version_gte("2.2.4", "1.2.3"))
        self.assertTrue(is_version_gte("1.3.4", "1.2.3"))
        self.assertFalse(is_version_gte("0.3.4", "1.2.3"))

        self.assertFalse(is_version_gte("0.3.4", "1.2.3"))

        with self.assertRaises(InvalidVersionFormat):
            is_version_gte("0.3.4.5", "0.3.4")

        with self.assertRaises(InvalidVersionFormat):
            is_version_gte("0.3.4", "0.3.4.5")

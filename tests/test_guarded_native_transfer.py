import sys
from fractions import Fraction
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from guarded_native.transfer import pair_certificate, scaled_ns


class NativeTransfer(unittest.TestCase):
    def test_anchor_rounds_once_after_scaling(self):
        self.assertEqual(scaled_ns(7, Fraction(3, 2), Fraction(7, 3)), 5)
        self.assertEqual(scaled_ns(2**50, 123, 123), 2**50)
        with self.assertRaises(ValueError):
            scaled_ns(7, 1, 0)

    def test_ratio_includes_intercept_zero_columns_and_ceil(self):
        bound, certificate = pair_certificate(100, 10, {"i": 1, "b": 0}, {"i": 2, "b": 0})
        self.assertEqual(bound, Fraction(89, 110))
        self.assertEqual(certificate["max_work_ratio"], "1")
        self.assertTrue(certificate["excludes_20pct"])
        self.assertIsNone(pair_certificate(100, 10, {"i": 1}, {"i": 0}))
        self.assertIsNone(pair_certificate(11, 10, {"i": 1}, {"i": 1}))

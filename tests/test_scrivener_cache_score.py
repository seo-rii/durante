from fractions import Fraction
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from scrivener_cache.score import fraction_in_range, predict, score_cases


class CacheForecast(unittest.TestCase):
    def test_fraction_handles_reverse_constant_and_outlying_endpoints(self):
        self.assertEqual(fraction_in_range(7, 3, 6), Fraction(1, 4))
        self.assertEqual(fraction_in_range(7, 3, 8), 0)
        self.assertEqual(fraction_in_range(7, 3, 2), 1)
        self.assertEqual(fraction_in_range(4, 4, 100), Fraction(1, 2))

    def test_forecasts_use_only_the_selected_feature_and_the_paid_labels(self):
        def row(x, miss, branch):
            return dict(position_bits=x, cachegrind=dict(DLmr=miss, Bcm=branch),
                        ordinary=dict(instructions=140, rep_bytes=8))
        a = dict(row(11, 7, 3), native_ns=[1000] * 9)
        b = dict(row(21, 3, 3), native_ns=[1009] * 9)
        held = row(19, 6, 13)
        self.assertEqual(predict(a, b, held),
                         dict(ordinary=1000, midpoint=1005, distance=1008, ll_read_misses=1003, branch_misses=1005))
        with self.assertRaisesRegex(ValueError, "native label"):
            predict(a, b, dict(held, native_ns=[1001] * 9))

    def test_native_equality_and_split_votes(self):
        for estimate, false_timeout in [(1_000_000, 0), (1_000_001, 1)]:
            predictions = dict.fromkeys(("ordinary", "midpoint", "distance", "ll_read_misses", "branch_misses"), estimate)
            cases = [dict(native_ns=[1_000_000] * 9, predictions=predictions),
                     dict(native_ns=[999_999] * 4 + [1_000_001] * 5, predictions=predictions)]
            result = score_cases(cases, [1])["ordinary"]
            self.assertEqual(result["mixed_limits"], 1)
            self.assertEqual(result["majority_correct"], 1)
            self.assertEqual(result["unanimous_false_timeout"], false_timeout)

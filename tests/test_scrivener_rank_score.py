from fractions import Fraction
from math import ceil
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from scrivener_rank import score
from scrivener_streams import score as original


def features(branch, disruptive, displacement, stale):
    return dict(work=100, position_bits=200, misses=10, bcm=branch, disruptive=disruptive,
                displacement=displacement, stale_disruptive=stale)


class ScrivenerRankScore(unittest.TestCase):
    def test_two_filters_cap_only_their_own_component_and_round_once(self):
        low, high = features(3, 10, 1000, 5), features(13, 20, 2200, 15)
        query = features(11, 16, 1200, 8)
        start, end = 2**54 + 1, 2**54 + 98
        for first, second in ((start, end), (end, start)):
            actual = score.forecasts(first, second, low, high, query)
            known = original.forecasts(first, second, low, high, query)
            self.assertEqual({k: actual[k] for k in known}, known)
            self.assertEqual(actual["midpoint_adjusted"], (known["midpoint"] + known["branch_stream"] + 1) // 2)
            for method, limit in (("branch_stale", Fraction(3, 10)), ("branch_displacement", Fraction(1, 6))):
                self.assertEqual(actual[method], ceil(first + (second - first) * (Fraction(8, 10) + limit) / 2))
            for endpoint, native in ((low, first), (high, second)):
                estimates = score.forecasts(first, second, low, high, endpoint)
                self.assertEqual([estimates[m] for m in score.FILTERS], [native, native])

    def test_clamping_reverse_anchors_and_constant_auxiliary_fallback(self):
        low, high = features(8, 9, 100, 7), features(2, 2, 40, 7)
        for observed in (features(13, 11, 150, 7), features(0, 0, 2, 7), features(5, 8, 40, 900)):
            result = score.forecasts(101, 119, low, high, observed)
            # The constant stale anchor adds no measured distinction; displacement exceeds the disruption cap here.
            self.assertEqual(result["branch_stale"], result["branch_stream"])
            self.assertEqual(result["branch_displacement"], result["branch_stream"])
        observed = features(2, 2, 100, 7)
        self.assertEqual(score.forecasts(101, 119, low, high, observed)["branch_displacement"], 110)

    def test_gate_requires_nonincreasing_exact_median_and_strict_worst(self):
        native = 10**12
        examples = {}
        for i, (old, new) in enumerate(zip([0, 0, 10, 10, 100], [0, 1, 11, 11, 95])):
            examples[str(i)] = {"solver": dict(median_native_ns=native, native_ns=[native] * 9, native_tle_votes={},
                                              predictions_ns=dict(branch_stream=native + old, branch_stale=native + new,
                                                                  midpoint_adjusted=native + old,
                                                                  branch_displacement=native + old))}
        result = score.qualification(examples, [])
        self.assertTrue(result["branch_stale"]["worst_error_improved"])
        self.assertFalse(result["branch_stale"]["median_error_nonincreasing"])
        self.assertFalse(result["branch_stale"]["accuracy_gate"])
        self.assertFalse(result["branch_displacement"]["worst_error_improved"])
        self.assertFalse(result["branch_displacement"]["accuracy_gate"])

    def test_fix_at_equality_and_new_false_pass_have_distinct_consequences(self):
        limit = 50_000_000
        examples = {}
        for name, actual, votes, old, stale, displacement in (
                ("fast", limit, 0, limit + 1, limit, limit),
                ("slow", limit + 100, 9, limit + 1, limit, limit + 1)):
            examples[name] = {"solver": dict(median_native_ns=actual, native_ns=[actual] * 9,
                                            native_tle_votes={"50": votes},
                                            predictions_ns=dict(branch_stream=old, midpoint_adjusted=old, branch_stale=stale,
                                                                branch_displacement=displacement))}
        result = score.qualification(examples, [50])
        self.assertFalse(result["branch_stale"]["deadline_gate"])
        self.assertTrue(result["branch_displacement"]["deadline_gate"])
        self.assertEqual(result["branch_stale"]["unanimous_error_changes"]["false_pass_added"], [["slow", "solver", 50]])
        self.assertEqual(result["branch_displacement"]["unanimous_error_changes"]["false_timeout_fixed"],
                         [["fast", "solver", 50]])

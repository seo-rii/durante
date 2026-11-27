from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from scrivener_streams.score import METHODS, forecasts, qualification, summarize


class ScrivenerStreamScore(unittest.TestCase):
    def test_normalization_uses_both_frozen_anchors_and_rounds_once(self):
        low = dict(work=4, position_bits=0, misses=0, bcm=2, disruptive=1)
        high = dict(work=6, position_bits=30, misses=16, bcm=10, disruptive=7)
        query = dict(work=4, position_bits=10, misses=16, bcm=4, disruptive=2)
        estimates = forecasts(100, 112, low, high, query)
        self.assertEqual({m: estimates[m] for m in ("pages", "branch_pages", "stream", "branch_stream")},
                         dict(pages=112, branch_pages=108, stream=102, branch_stream=103))
        self.assertEqual(forecasts(112, 100, low, high, dict(query, disruptive=99))["stream"], 100)
        self.assertEqual(forecasts(100, 112, low, dict(high, disruptive=1), query)["stream"], 106)

    def test_prior_regression_is_scored_but_cannot_affect_holdout_statistics(self):
        plan = dict(cases=[dict(name="lanes32", group="control"), dict(name="fresh", group="holdout")],
                    binaries={"compact": "elf"}, fixed_limits_ms=list(range(30, 86, 5)))
        predictions = {n: {"compact": dict.fromkeys(METHODS, ns)} for n, ns in
                       (("lanes32", 10**9), ("fresh", 30_000_000))}
        rows = [dict(arm="native", name=n, program="compact", cpu_us=30_000)
                for n in predictions for _ in range(9)]
        report = summarize(plan, rows, predictions)
        self.assertGreater(report["cases"]["lanes32"]["compact"]["signed_error_ppm"]["stream"], 0)
        self.assertEqual(report["summary"]["stream"]["cases"], 1)
        self.assertEqual(report["summary"]["stream"]["worst_absolute_error_ppm"], 0)
        self.assertEqual(report["summary"]["stream"]["unanimous_false_timeout"], 0)

    def test_a_new_false_pass_fails_the_deadline_gate_despite_fixing_a_timeout(self):
        def case(ns, old, new, votes):
            return dict(median_native_ns=ns, native_ns=[ns] * 9, native_tle_votes={"65": votes},
                        predictions_ns=dict(pages=old, branch_pages=old, stream=new, branch_stream=new))
        cases = {"a": {"compact": case(60_000_000, 70_000_000, 65_000_000, 0)},
                 "b": {"compact": case(66_000_000, 66_000_000, 65_000_000, 9)}}
        comparison = qualification(cases, [65])["branch_stream"]
        self.assertTrue(comparison["accuracy_gate"])
        self.assertFalse(comparison["deadline_gate"])
        self.assertEqual(comparison["unanimous_error_changes"]["false_pass_added"], [["b", "compact", 65]])

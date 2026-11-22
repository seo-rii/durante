from fractions import Fraction
from pathlib import Path
import runpy
import unittest

score = runpy.run_path(str(Path(__file__).resolve().parents[1] / "experiments/guarded_native/score.py"))
MS = 1_000_000


class GuardedNativeScoring(unittest.TestCase):
    def test_exact_nnls_with_collinear_and_unobserved_columns(self):
        rows = {"a": {"delivered": [{"x": 1, "also_x": 1, "zero": 0}]},
                "b": {"delivered": [{"x": 3, "also_x": 3, "zero": 0}]}}
        result = score["fit"](["a", "b"], ["x", "also_x", "zero"], rows, {"a": [2], "b": [6]}, "delivered")
        self.assertEqual(result["rank"], 1)
        self.assertTrue(result["rank_deficient"])
        self.assertEqual(result["weights_ns"], {"x": "2", "also_x": "0", "zero": "0"})

    def test_whole_ns_ceil_and_strict_limit(self):
        equal = score["predict"]({"x": Fraction(5 * MS, 2)}, {"x": 2})
        above = score["predict"]({"x": Fraction(50 * MS + 1, 10)}, {"x": 1})
        self.assertEqual((equal, above), (5 * MS, 5 * MS+1))
        self.assertEqual(score["boundary"]([equal], [equal], 5)["classification"], "agreement")
        self.assertEqual(score["boundary"]([equal], [equal, above], 5)["classification"], "false_tle")
        self.assertEqual(score["boundary"]([above], [equal, above], 5)["classification"], "false_accept")
        self.assertEqual(score["boundary"]([equal, above], [above], 5)["classification"], "mixed_native")

    def test_declined_paired_full_is_not_an_actual_rerun(self):
        trial = {"kind": "scored", "delivery_source": "matched_full_substitute", "delivered": 1, "full": 1}
        audit = {"kind": "audit", "delivery_source": "actual_full_recovery", "delivered": 2, "full": None}
        self.assertEqual(score["views"]({"rows": [trial, audit]}), {"full": [1], "delivered": [2]})

    def test_case_level_inclusive_accuracy_and_submillisecond_limits(self):
        ret = score["score_case"]([10*MS], [8*MS, 12*MS], [1,10])
        self.assertTrue(ret["median"]["within_20pct"])
        self.assertEqual([b["limit_ms"] for b in ret["relative"]], [8, 12])
        self.assertFalse(score["score_case"]([10*MS], [12*MS+1], [])["median"]["within_20pct"])
        tiny = score["score_case"]([500_000], [400_000], [50])
        self.assertEqual(tiny["relative"], [])
        self.assertEqual(score["summarize"]({"x":tiny}, ["x"])["excluded_relative_boundaries"], 2)

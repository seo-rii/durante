from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from scrivener_pages.score import METHODS, forecasts, summarize


class ScrivenerPageScore(unittest.TestCase):
    def test_normalize_each_feature_then_average_and_round_once(self):
        low = dict(work=3, position_bits=10, misses=0, bcm=8)
        high = dict(work=9, position_bits=10, misses=8, bcm=0)
        holdout = dict(work=4, position_bits=100, misses=1, bcm=5)
        self.assertEqual(forecasts(100, 104, low, high, holdout),
                         dict(midpoint=102, ordinary=134, distance=102, pages=101, branch=102, branch_pages=101))
        self.assertEqual(forecasts(104, 100, low, high, dict(holdout, misses=800, bcm=100))["branch_pages"], 102)
        with self.assertRaisesRegex(ValueError, "native label"):
            forecasts(100, 104, low, high, dict(holdout, native_ns=[100]))

    def test_scores_compare_each_solver_and_count_shared_scan_once(self):
        plan = dict(cases=[dict(name="low", group="anchor"), dict(name="test", group="holdout")],
                    binaries={"compact": "a", "lifting": "b"}, fixed_limits_ms=list(range(30, 86, 5)))
        samples = {"compact": [29_999] * 4 + [30_001] * 5, "lifting": [30_000] * 9}
        rows = [dict(name="test", program=prog, rep=i, arm="native", cpu_us=t)
                for prog, times in samples.items() for i, t in enumerate(times, 1)]
        rows += [dict(name="test", program="scan", rep=i, arm="scan", cpu_us=t)
                 for i, t in enumerate((17, 13, 11), 1)]
        rows.append(dict(name="low", program="compact", rep=1, arm="native", cpu_us=10_000))
        pred = {"test": {"compact": dict.fromkeys(METHODS, 30_000_000),
                         "lifting": dict.fromkeys(METHODS, 36_000_000)}}
        report = summarize(plan, rows, pred)
        self.assertEqual(report["profile_cost"]["scan"], dict(runs=3, total_cpu_us=41, median_cpu_us=13))
        self.assertEqual(report["profile_cost"]["native"]["runs"], 19)
        case = report["cases"]["test"]["lifting"]
        self.assertEqual(case["signed_error_ppm"]["pages"], 200_000)
        self.assertTrue(case["within_20pct"]["pages"])
        result = report["summary"]["branch_pages"]
        self.assertEqual(result["fixed_limits"], 24)
        self.assertEqual(result["majority_false_timeout"], 2)
        self.assertEqual(result["majority_false_pass"], 1)
        self.assertEqual(result["unanimous_false_timeout"], 2)
        self.assertEqual(result["unanimous_false_pass"], 0)
        self.assertEqual(result["mixed_limits"], 1)
        self.assertEqual(result["within_20pct"], 2)

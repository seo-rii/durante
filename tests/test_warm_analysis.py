"""Check paired error comparisons, strict deadline projections and live-versus-implied separation."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from contest_warm.analysis import analyze, case_record, duration, median_index, summarize
from guarded_native.score import MS
from shape_calibration.verify_combined import unpack


class WarmSummary(unittest.TestCase):
    def test_stable_time_ties_and_strict_projected_limit(self):
        self.assertEqual(median_index([9, 5, 5, 7], (1, 2, 3)), 2)
        self.assertEqual(median_index([5, 1, 5, 7], (0, 1, 2)), 0)
        trial = dict(program="example", cohort="prospective", profile_cpu_ns=1,
                     baseline_ns=MS+3, baseline_paid_cpu_ns=MS+3, extra_ns=[MS, MS+1, MS],
                     packet_warm_ns=MS, native_ns=[MS]*6 + [MS+1]*5, deadlines=[
                         dict(limit_ms=1, fixed=True, adaptive_percent=[100], native_tle_reps=5,
                              baseline=dict(tle=True), warm=dict(tle=True))])
        c = case_record(trial)
        point = c["deadlines"][0]
        self.assertEqual(point["actual_tle"], {"baseline":True, "warm":True})
        self.assertEqual(point["model_implied_tle"], {"first_postwarm":False, "median_first_three":True})
        summary = summarize([c])
        self.assertEqual(summary["fixed_adaptive_overlap"], 1)
        self.assertEqual(summary["all"]["actual"]["warm"]["majority_correct"], 0)
        self.assertEqual(summary["all"]["model_implied"]["first_postwarm"]["majority_correct"], 1)
        self.assertEqual(summary["all"]["minimum_disagreements_with_repetitions"], 5)
        self.assertEqual(summary["extra_native_cpu_ns"], 3*MS+1)

    def test_paired_wins_ties_and_losses_before_rounding(self):
        ref = 10**9
        cases = [dict(observations_ns=[ref+a, ref+b], selected_indices={"baseline":0, "warm":1}, native_ns=[ref])
                 for a,b in [(2, -1), (1,-1), (0,1)]]
        report = duration(cases, "warm")
        self.assertEqual(report["compared_with_baseline"], {"wins":1, "ties":1, "losses":1})
        self.assertEqual(report["median_absolute_error_ppm"], 0)

    def test_published_report_regenerates_from_raw_observations(self):
        result = ROOT / "experiments/results/gce-contest-warm"
        with tempfile.TemporaryDirectory() as tmp:
            raw = unpack(result / "output.tar.xz", Path(tmp))
            report = analyze(raw)
        self.assertEqual(report, json.loads((result / "analysis.json").read_bytes()))
        self.assertEqual(report["cohorts"]["prospective"]["inputs"], 12)
        self.assertEqual(report["cohorts"]["previous"]["inputs"], 2)
        self.assertEqual(report["all"]["extra_native_runs"], 42)
        self.assertEqual(set(report["programs"]), {"dreaming", "scrivener", "posterize", "money", "wall"})
        for key,case in report["cases"].items():
            with self.subTest(key=key):
                self.assertEqual(len(case["native_ns"]), 11)
                for point in case["deadlines"]:
                    expected = sum(t > point["limit_ms"] * MS for t in case["native_ns"])
                    self.assertEqual(point["native_tle"], expected)

    def test_interleaved_archives_contain_only_the_two_registered_arms(self):
        for suffix in ("gce-contest-batch", "gce-contest-batch-repeat"):
            directory = ROOT / "experiments/results" / suffix
            with self.subTest(guest=suffix), tempfile.TemporaryDirectory() as temp:
                raw = unpack(directory / "output.tar.xz", Path(temp))
                result = analyze(raw, "interleaved-three")
                self.assertEqual(result, json.loads((directory / "analysis.json").read_bytes()))
                self.assertEqual(result["cohorts"]["prospective"]["inputs"], 10)
                self.assertEqual(result["cohorts"]["previous"]["inputs"], 2)
                self.assertNotIn("model_implied", json.dumps(result))
                self.assertEqual(set(result["all"]["actual_duration"]), {"baseline", "warm"})
                self.assertEqual(result["all"]["extra_native_runs"], 36)
                for case in result["cases"].values():
                    self.assertEqual(set(case["selected_indices"]), {"baseline", "warm"})
                    self.assertEqual(case["observations_ns"][case["selected_indices"]["warm"]],
                                     sorted(case["observations_ns"][1:])[1])

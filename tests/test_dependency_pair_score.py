from pathlib import Path
from unittest.mock import patch
import runpy
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "experiments/dependency_pairs"
RESULTS = ROOT / "experiments/results/gce-dependency-pairs"


class DependencyPairScore(unittest.TestCase):
    def test_frozen_predictions_use_sixteenths_and_score_every_rep(self):
        with patch.object(sys, "path", [str(STUDY), *sys.path]):
            score = runpy.run_path(str(STUDY / "score.py"))
        rows = [{"native_before": {"cpu_time_ns": 90}, "native_after": {"cpu_time_ns": 110},
                 "operand_bytes": {"production": {"instructions": 32, "rep_bytes": 0,
                      "latency_bound": {"cycles_x16": cycles}, "memory_operand_bytes": {"bytes": 4}}}}
                for cycles in [144, 144, 144, 192]]
        result = score["summarize"](rows, {"latency_cycles": "10", "memory_bytes": "5/2"},
                                    {"min_native_ns": 20, "max_spread_ppm": 100000, "max_error_ppm": 200000})
        self.assertEqual(result["predicted_ns"], {"min": "100", "median": "100", "max": "130"})
        self.assertEqual(result["errors_ppm"], [0, 0, 0, 300000])
        self.assertFalse(result["accuracy_pass"])
        self.assertFalse(result["spread_pass"])
        self.assertEqual(result["round_native_midpoints_ns"], ["100"] * 4)

    def test_published_results_replay(self):
        check = subprocess.run([sys.executable, STUDY / "score.py", RESULTS / "dataset.json.gz",
                                "--verify", RESULTS / "evaluation.json"], capture_output=True, text=True, timeout=60)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)


if __name__ == "__main__":
    unittest.main()

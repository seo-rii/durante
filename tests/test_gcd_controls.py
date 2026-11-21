"""Replay the pinned GCD study and check the online probe against the input oracle."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "experiments/gcd_controls"
DATA = ROOT / "experiments/results/gce-gcd-controls"


class GcdControlStudy(unittest.TestCase):
    def run_script(self, script, *args):
        result = subprocess.run(
            [sys.executable, STUDY / script, *args],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_archived_inputs_and_calibration(self):
        self.run_script("stream/fixtures.py", "--verify")
        self.run_script("loops/fixtures.py")
        self.run_script("calibrate.py")

    def test_receipts_placement_and_scores(self):
        dataset = DATA / "dataset.json.gz"
        self.run_script("verify.py", dataset, DATA / "receipts.tar.xz")
        self.run_script("placement.py", dataset, DATA / "placement.json.gz", "--platform", DATA / "platform.json")
        self.run_script("score.py", "--verify")

    def test_measured_loop_counts_match_source(self):
        plan = json.loads((STUDY / "evaluation-plan.json").read_bytes())
        population = json.loads((STUDY / "calibration.json").read_bytes())["population"]
        scores = json.loads((DATA / "evaluation.json").read_bytes())
        for case in (*plan["groups"]["anchor"], *plan["groups"]["loop"], *plan["groups"]["stream"]):
            with self.subTest(case=case):
                stream = case in plan["groups"]["stream"]
                order = "grouped" if case.endswith("_grouped") else "original"
                expected = {
                    "visits": population["divisions"], "sites": 1, "pending": 0,
                    "chain_exit": 1 if stream else population["chains"],
                    "length_change": 0 if stream else population[f"{order}_changes"],
                }
                self.assertEqual(scores["cases"][case]["observed_counts"], [expected] * 4)


if __name__ == "__main__":
    unittest.main()

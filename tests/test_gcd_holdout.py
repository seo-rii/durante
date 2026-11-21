"""Replay the frozen Euclidean timing rule and all prospective evidence."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "experiments/gcd_holdout"
DATA = ROOT / "experiments/results/gce-gcd-controls/holdout"


class GcdHoldoutStudy(unittest.TestCase):
    def run_script(self, script, *args):
        result = subprocess.run([sys.executable, STUDY / script, *args], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_frozen_fit_classes_and_input_oracles(self):
        self.run_script("fit.py")
        self.run_script("replay_training.py", "--verify")
        self.run_script("fixtures.py")
        self.run_script("test_classify.py")

    def test_native_receipts_and_scores(self):
        raw = DATA / "dataset.json.gz"
        self.run_script("verify.py", raw, DATA / "receipts.tar.xz")
        self.run_script("placement.py", raw, DATA / "placement.json.gz", "--platform", DATA / "platform.json")
        self.run_script("score.py", "--verify")

    def test_selected_site_matches_each_input_oracle(self):
        fixtures = json.loads((DATA / "fixtures.json").read_bytes())
        report = json.loads((DATA / "evaluation.json").read_bytes())
        for case in fixtures["cases"]:
            name = case["workload_id"]
            oracle = case["oracle"]
            for observed in report["cases"][name]["counts"]:
                with self.subTest(case=name):
                    self.assertEqual(observed["eligible_visits"], oracle["divisions"])
                    self.assertEqual(observed["remainder_length_changes"], oracle["changes"])
                    self.assertEqual([site["mode"] for site in observed["sites"]], ["remainder"])


if __name__ == "__main__":
    unittest.main()

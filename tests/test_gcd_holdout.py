"""Replay the frozen Euclidean timing rule and all prospective evidence."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

from registered_tree import registered_root

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "experiments/gcd_holdout"
DATA = ROOT / "experiments/results/gce-gcd-controls/holdout"
PROTOCOL = "experiments/gcd_holdout/protocol.json"


class GcdHoldoutStudy(unittest.TestCase):
    def run_script(self, script, *args, study=STUDY):
        result = subprocess.run([sys.executable, study / script, *args], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_frozen_fit_classes_and_input_oracles(self):
        study = registered_root(self, PROTOCOL) / STUDY.relative_to(ROOT)
        for script, *args in (("fit.py",), ("replay_training.py", "--verify"), ("fixtures.py",),
                              ("test_classify.py",)):
            self.run_script(script, *args, study=study)

    def test_native_receipts_and_scores(self):
        root = registered_root(self, PROTOCOL)
        study, data = root / STUDY.relative_to(ROOT), root / DATA.relative_to(ROOT)
        raw = data / "dataset.json.gz"
        self.run_script("verify.py", raw, data / "receipts.tar.xz", study=study)
        self.run_script("placement.py", raw, data / "placement.json.gz", "--platform",
                        data / "platform.json", study=study)
        self.run_script("score.py", "--verify", study=study)

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

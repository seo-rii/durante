"""Replay both the premeasurement fit and its prospective client evaluation."""
from pathlib import Path
import subprocess
import sys
import unittest

from registered_tree import registered_root

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "experiments/divide_clock"
DATA = ROOT / "experiments/results/divide-clock"


class DividerStudy(unittest.TestCase):
    def check_script(self, script, *args, study=STUDY):
        result = subprocess.run([sys.executable, study/script, *args], capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_historical_fit_and_operand_oracle(self):
        self.check_script("score.py", "--verify")
        self.check_script("check_operands.py", "--verify", DATA/"operand-profiles.json")
        self.check_script("feasibility.py", "--verify")

    def test_fresh_raw_evidence_and_frozen_predictions(self):
        root = registered_root(self, "experiments/divide_clock/fresh/protocol.json")
        study = root / STUDY.relative_to(ROOT)
        directory = root / DATA.relative_to(ROOT) / "fresh"
        dataset = directory / "dataset.json.gz"
        self.check_script("fresh/fixtures.py", "--verify", study=study)
        self.check_script("fresh/verify.py", dataset, directory/"receipts.tar.xz", study=study)
        self.check_script("fresh/placement.py", dataset, directory/"placement.json.gz", "--platform",
                          directory/"platform.json", study=study)
        self.check_script("fresh/score_fresh.py", "--verify", study=study)


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "experiments/cpp20_locality"
RESULT = ROOT / "experiments/results/gce-cpp20-locality"


class LocalityReplay(unittest.TestCase):
    def test_public_receipts_placement_and_scores(self):
        for script, args in (
            ("verify.py", [RESULT]),
            ("score.py", [RESULT / "dataset.json.gz", RESULT / "receipts.tar.xz",
                          "--verify", RESULT / "evaluation.json"]),
        ):
            with self.subTest(script=script):
                run = subprocess.run([sys.executable, "-B", STUDY / script, *args], capture_output=True,
                                     text=True, timeout=60)
                self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "experiments/first_run_sampler"
DATA = ROOT / "experiments/results/gce-first-run-sampler"


class FirstRunReceipts(unittest.TestCase):
    def test_published_clocks_profiles_and_cost(self):
        for command in ([STUDY / "verify.py", DATA],
                        [STUDY / "score.py", DATA / "dataset.json.gz", DATA / "receipts.tar.xz",
                         "--verify", DATA / "evaluation.json"]):
            with self.subTest(script=command[0].name):
                process = subprocess.run([sys.executable, "-B", *command], capture_output=True, text=True, timeout=60)
                self.assertEqual(process.returncode, 0, process.stderr)

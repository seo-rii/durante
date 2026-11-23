from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "experiments/guarded_native"
DATA = ROOT / "experiments/results/gce-guarded-native"


class GuardedNativeReplay(unittest.TestCase):
    def test_published_native_timings_and_estimators(self):
        dataset = DATA / "dataset.json.gz"
        commands = (
            ("verify.py", [DATA]),
            ("score.py", [dataset, "--verify", DATA / "evaluation.json.gz"]),
            ("transfer.py", [dataset, "--verify", DATA / "transfer.json"]),
        )
        for script, args in commands:
            with self.subTest(script=script):
                process = subprocess.run([sys.executable, "-B", STUDY / script, *args],
                                         capture_output=True, text=True, timeout=60)
                self.assertEqual(process.returncode, 0, process.stderr)

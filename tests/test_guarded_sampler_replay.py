from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "experiments/guarded_sampler"
RESULT = ROOT / "experiments/results/gce-guarded-sampler"


class GuardedSamplerReplay(unittest.TestCase):
    def test_published_recovery_decisions_and_work_clocks(self):
        for command in ([STUDY / "verify.py", RESULT], [STUDY / "score.py", RESULT / "dataset.json.gz",
                        RESULT / "receipts.tar.xz", "--verify", RESULT / "evaluation.json"]):
            with self.subTest(script=command[0].name):
                process = subprocess.run([sys.executable, "-B", *command], capture_output=True, text=True, timeout=60)
                self.assertEqual(process.returncode, 0, process.stderr)

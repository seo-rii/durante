from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "experiments/live_sampler_cost"
RESULT = ROOT / "experiments/results/gce-live-sampler-cost"


class LiveSamplerReplay(unittest.TestCase):
    def test_published_clocks_and_costs(self):
        for script, args in (
            ("verify.py", [RESULT]),
            ("score.py", [RESULT / "dataset.json.gz", RESULT / "receipts.tar.xz",
                          "--verify", RESULT / "evaluation.json"]),
        ):
            with self.subTest(script=script):
                completed = subprocess.run([sys.executable, "-B", STUDY / script, *args],
                                           capture_output=True, text=True, timeout=60)
                self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "experiments/live_sampler_fast"
RESULT = ROOT / "experiments/results/gce-live-sampler-fast"


class LiveSamplerFastReplay(unittest.TestCase):
    def test_clocks_selection_and_cost(self):
        for script, args in (
            ("verify.py", [RESULT]),
            ("score.py", [RESULT / "dataset.json.gz", RESULT / "receipts.tar.xz", "--verify", RESULT / "evaluation.json"]),
        ):
            with self.subTest(script=script):
                process = subprocess.run([sys.executable, "-B", STUDY / script, *args],
                                         capture_output=True, text=True, timeout=60)
                self.assertEqual(process.returncode, 0, process.stderr)

    def test_unregistered_program_has_no_site_list(self):
        process = subprocess.run([sys.executable, "-B", STUDY / "profiles.py", "--sites-for", __file__],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 2)
        self.assertIn("no sampled-site profile registered", process.stderr)
        self.assertEqual(process.stdout, "")


if __name__ == "__main__":
    unittest.main()

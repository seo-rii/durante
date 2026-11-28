import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from repeatability import probe_repeatable

HERE = Path(__file__).resolve().parent


class ProbeRepeatabilityTests(unittest.TestCase):
    def test_samples(self):
        sample = {"instructions": 10, "l1d_refs": 2, "probe_cpu_s": 0.1, "probe_wall_s": 0.12}
        self.assertIsNone(probe_repeatable([]))
        self.assertIsNone(probe_repeatable([sample]))
        self.assertIsNone(probe_repeatable([{}, {}]))
        other = {**sample, "probe_cpu_s": 0.2, "probe_wall_s": 0.3}
        self.assertIs(probe_repeatable([sample, other]), True)
        self.assertIs(probe_repeatable([sample, other, {**sample, "l1d_refs": 3}]), False)
        self.assertIs(probe_repeatable([sample, {**other, "l1d_dependent": 0}]), False)

    def test_uncounted_collection_is_rejected_before_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "collection"
            for count in ("0", "-1"):
                run = subprocess.run([sys.executable, "-B", str(HERE / "collect.py"), "--sdk", tmp,
                                      "--client", tmp, "--output-dir", str(output), "--probe-runs", count],
                                     capture_output=True, text=True)
                self.assertEqual(run.returncode, 2)
                self.assertIn("--probe-runs must be positive", run.stderr)
                self.assertFalse(output.exists())

    def test_archived_runs(self):
        for version, count, expected in (("v1", 36, True), ("v2", 85, None)):
            with self.subTest(version=version):
                cases = json.loads((HERE / "results" / version / "results.json").read_bytes())["cases"]
                self.assertEqual(len(cases), count)
                for case in cases.values():
                    self.assertIs(probe_repeatable(case["probe"]), expected)


if __name__ == "__main__":
    unittest.main()

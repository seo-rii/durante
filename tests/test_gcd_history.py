"""Exercise the history client against hand-counted loops and the pinned guest evidence."""
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest

from registered_tree import registered_root

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "experiments/gcd_history"
DATA = ROOT / "experiments/results/gce-gcd-history"


class GcdHistoryStudy(unittest.TestCase):
    def check_command(self, *args):
        process = subprocess.run(args, capture_output=True, text=True, timeout=120)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)

    def test_hand_counted_successors(self):
        sdk = Path(os.environ.get("DURANTE_TEST_SDK", ROOT / "toolkit/sdk/DynamoRIO-Linux-11.3.0-1")).resolve()
        launcher = sdk / "bin64/drrun"
        if os.environ.get("DURANTE_TEST_BUNDLE"):
            launcher = Path(os.environ["DURANTE_TEST_BUNDLE"]) / "dynamorio/bin64/drrun"
        if platform.machine() != "x86_64" or not (sdk / "cmake").is_dir() or not launcher.is_file():
            self.skipTest("requires an x86-64 DynamoRIO SDK and launcher")
        with tempfile.TemporaryDirectory(prefix="durante-history-test-") as tmp:
            self.check_command("cmake", "-S", STUDY / "probe", "-B", tmp, "-DCMAKE_BUILD_TYPE=Release",
                               f"-DDynamoRIO_DIR={sdk}/cmake")
            self.check_command("cmake", "--build", tmp, "--target", "history_probe")
            self.check_command(sys.executable, STUDY / "probe/check.py", launcher, Path(tmp) / "libhistory_probe.so")

    def test_pinned_timing_and_placement(self):
        root = registered_root(self, "experiments/gcd_history/protocol.json")
        study, data = root / STUDY.relative_to(ROOT), root / DATA.relative_to(ROOT)
        raw = data / "dataset.json.gz"
        self.check_command(sys.executable, study / "verify.py", raw, data / "receipts.tar.xz")
        self.check_command(sys.executable, study / "placement.py", raw, data / "placement.json.gz",
                           "--platform", data / "platform.json")
        self.check_command(sys.executable, study / "score.py", "--verify")

    def test_counters_match_source_oracles(self):
        self.check_command(sys.executable, STUDY / "fixtures.py")
        scores = json.loads((DATA / "evaluation.json").read_bytes())["cases"]
        for case in json.loads((DATA / "fixtures.json").read_bytes())["cases"]:
            oracle = case["oracle"]
            for run in scores[case["workload_id"]]["counts"]:
                with self.subTest(workload=case["workload_id"]):
                    self.assertEqual(run["eligible_visits"], oracle["divisions"])
                    self.assertEqual(run["remainder_length_changes"], oracle["changes"])
                    self.assertEqual(run["unpredicted_changes"], oracle["unpredicted_changes"])
                    self.assertEqual([s["mode"] for s in run["sites"]], ["remainder"])


if __name__ == "__main__":
    unittest.main()

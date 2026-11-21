"""Verify the endpoint probe, control fixtures and pinned binary-search evidence."""
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "experiments/search_order"
DATA = ROOT / "experiments/results/gce-search-order"


class SearchOrderStudy(unittest.TestCase):
    def run_check(self, *command):
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_live_sample_and_comparison_flags(self):
        sdk = Path(os.environ.get("DURANTE_TEST_SDK", ROOT / "toolkit/sdk/DynamoRIO-Linux-11.3.0-1")).resolve()
        launcher = sdk / "bin64/drrun"
        if os.environ.get("DURANTE_TEST_BUNDLE"):
            launcher = Path(os.environ["DURANTE_TEST_BUNDLE"]) / "dynamorio/bin64/drrun"
        if platform.machine() != "x86_64" or not (sdk/"cmake").is_dir() or not launcher.is_file():
            self.skipTest("requires an x86-64 DynamoRIO SDK and launcher")
        with tempfile.TemporaryDirectory(prefix="durante-search-test-") as tmp:
            self.run_check("cmake", "-S", STUDY/"probe", "-B", tmp, "-DCMAKE_BUILD_TYPE=Release", f"-DDynamoRIO_DIR={sdk}/cmake")
            self.run_check("cmake", "--build", tmp, "--target", "search_probe")
            self.run_check(sys.executable, STUDY/"probe/check.py", launcher, Path(tmp)/"libsearch_probe.so")

    def test_registered_queries_and_oracles(self):
        self.run_check(sys.executable, STUDY/"fixtures.py")
        inputs = {c["workload_id"]: c for c in json.loads((DATA/"fixtures.json").read_bytes())["cases"]}
        matched = [inputs[f"{mode}_{order}"]["oracle"] for mode in ("cmov","jcc") for order in ("sorted","random","block32")]
        self.assertEqual(len({o["sorted_targets_sha256"] for o in matched}), 1)
        self.assertEqual(len({o["checksum"] for o in matched}), 1)
        for order in ("sorted","random","block32"):
            self.assertEqual(inputs[f"cmov_{order}"]["input_sha256"], inputs[f"jcc_{order}"]["input_sha256"])
            self.assertEqual(inputs[f"cmov_{order}"]["oracle"], inputs[f"jcc_{order}"]["oracle"])

    def test_raw_wait4_records_placement_and_predictions(self):
        dataset = DATA / "dataset.json.gz"
        self.run_check(sys.executable, STUDY/"verify.py", dataset, DATA/"receipts.tar.xz")
        self.run_check(sys.executable, STUDY/"placement.py", dataset, DATA/"placement.json.gz", "--platform", DATA/"platform.json")
        self.run_check(sys.executable, STUDY/"score.py", "--verify")


if __name__ == "__main__":
    unittest.main()

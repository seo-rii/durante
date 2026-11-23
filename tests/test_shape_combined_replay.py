import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from cpp20_locality.verify import sha
from shape_calibration.verify_combined import unpack, verify

RESULT = ROOT / "experiments/results/gce-shape-combined"


class CombinedReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.output = unpack(RESULT / "output.tar.xz", Path(cls.temp.name))

    def test_reproduces_cpu_and_predicate_comparisons(self):
        metrics = verify(self.output)
        self.assertEqual(metrics, json.loads((RESULT / "metrics.json").read_bytes()))
        expected = json.loads((RESULT / "collection.json").read_bytes())
        self.assertEqual(sha((RESULT / "output.tar.xz").read_bytes()), expected["output_sha256"])
        self.assertEqual(metrics["pids_verified"], 117)
        self.assertEqual(metrics["totals"]["all"]["cutoffs"]["paired"]["decisions"], 120)
        self.assertEqual(metrics["totals"]["holdout"]["distinct_limits"], 40)

    def test_rejects_double_charging_one_process(self):
        path = self.output / "summary.json"
        before = path.read_bytes()
        try:
            summary = json.loads(before)
            arm = summary["rows"][0]["arms"]["combined"]
            arm["target_cpu_ns"] += arm["temporal_cpu_ns"]
            path.write_text(json.dumps(summary))
            with self.assertRaisesRegex(ValueError, "number of wait4 charges"):
                verify(self.output)
        finally:
            path.write_bytes(before)

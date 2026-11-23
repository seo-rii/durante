import hashlib
import json
import lzma
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from shape_calibration.verify import verify

RESULT = ROOT / "experiments/results/gce-shape-calibration"


class ShapeReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        with tarfile.open(RESULT / "output.tar.xz", "r:xz") as archive:
            for item in archive:
                path = PurePosixPath(item.name)
                if not item.isfile() or not path.parts or path.parts[0] != "output" or ".." in path.parts:
                    raise ValueError("unexpected archive member")
                dest = Path(cls.temp.name) / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(item) as src, dest.open("xb") as saved:
                    shutil.copyfileobj(src, saved)
        cls.output = Path(cls.temp.name) / "output"

    def test_matches_published_metrics(self):
        metrics = verify(self.output)
        self.assertEqual(metrics, json.loads((RESULT / "metrics.json").read_bytes()))
        collection = json.loads((RESULT / "collection.json").read_bytes())
        self.assertEqual(hashlib.sha256((RESULT / "output.tar.xz").read_bytes()).hexdigest(), collection["output_sha256"])
        probe = lzma.decompress((RESULT / "temporal.so.xz").read_bytes())
        self.assertEqual(hashlib.sha256(probe).hexdigest(), metrics["probe_sha256"])
        self.assertEqual(collection["input_sources_sha256"]["experiments/shape_calibration/rules.json"], metrics["rules_sha256"])
        self.assertEqual(metrics["native_runs"], 9 * metrics["totals"]["all"]["inputs"])
        for case in metrics["cases"].values():
            self.assertEqual(len(case["native_ns"]), 9)
            for method in ("shape", "temporal"):
                uses = case[method]
                if uses["action"] == "reuse":
                    self.assertEqual(uses["paid_native_ns"], 0)
                else:
                    self.assertEqual(uses["paid_native_ns"], uses["prediction_ns"])
        self.assertEqual(metrics["totals"]["all"]["live"]["limits"], 40)

    def test_native_reference_stdout_is_verified(self):
        path = self.output / "native/000/stdout"
        before = path.read_bytes()
        try:
            path.write_bytes(before + b"unexpected output")
            with self.assertRaisesRegex(ValueError, "wrong direct native output"):
                verify(self.output)
        finally:
            path.write_bytes(before)

    def test_stored_policy_choice_is_verified(self):
        first = next((self.output / "cases").glob("*/*/selection.json"))
        before = first.read_bytes()
        try:
            saved = json.loads(before)
            saved["temporal"]["prediction_ns"] += 1
            first.write_text(json.dumps(saved))
            with self.assertRaisesRegex(ValueError, "selection disagrees"):
                verify(self.output)
        finally:
            first.write_bytes(before)

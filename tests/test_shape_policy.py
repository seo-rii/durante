import gzip
import json
from pathlib import Path
import platform
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from shape_calibration import select_policy as select


@unittest.skipUnless(sys.platform == "linux" and platform.machine() == "x86_64", "requires the static Linux fixture")
class ShapePolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        cls.elf = root / "path_mix"
        study = ROOT / "experiments/guarded_sampler"
        data = ROOT / "experiments/results/gce-guarded-sampler"
        with tarfile.open(study / "control/elf.tar.xz") as t:
            cls.elf.write_bytes(t.extractfile("bin/path_mix").read())
        cls.elf.chmod(0o700)
        rows = json.loads(gzip.decompress((data / "dataset.json.gz").read_bytes()))["rows"]
        cases = {c["case_id"]: c for c in json.loads((study / "cases.json").read_bytes())}
        with tarfile.open(data / "receipts.tar.xz", "r:xz") as t:
            for name, seq in (("first", 6), ("shift", 49), ("other", 90)):
                row = rows[seq]
                case = cases[row["case_id"]]
                assert case["program"] == "path_mix" and row["arm"] in ("calibration", "full")
                directory = root / name / "full"
                directory.mkdir(parents=True)
                (directory / "stdin").write_text(case["stdin"])
                launch = {k: row[k] for k in ("exit_code", "launcher_pid", "stdout_sha256")}
                report = dict(elf_sha256=case["binary_sha256"], stdin_sha256=case["stdin_sha256"],
                              authority="diagnostic_only", launches=[{"mode": "full", **launch}],
                              final_sample="full/sample.json", stdout="full/stdout")
                (directory / "result.json").write_text(json.dumps(report))
                for path in row["receipts"]:
                    dest = directory / "full" / Path(path).relative_to(f"runs/{seq:03d}")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(t.extractfile(path).read())

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name in ("first", "shift", "other"):
            shutil.copytree(Path(self.temp.name) / name, self.root / name)

    def select(self, current, refs=(), method="shape"):
        return select.resolve(self.elf, self.root / current, [self.root / n for n in refs], "0" * 64, method)

    def test_new_shape_pays_once_and_later_matching_input_reuses_its_clock(self):
        initial = self.select("first")
        new = self.select("shift", ["first"])
        self.assertEqual((initial["action"], new["action"]), ("calibrate", "calibrate"))
        with patch.object(select.anchor, "native_once", side_effect=AssertionError("duplicate native execution")):
            same = self.select("shift", ["first"])
            self.assertEqual(new, same)
            later = self.select("other", ["first", "shift"])
        self.assertEqual((later["action"], later["reference_index"], later["policy_sha256"]),
                         ("reuse", 1, new["policy_sha256"]))
        self.assertEqual([c["shape"]["status"] for c in later["comparisons"]], ["needs_native", "reuse"])
        self.assertFalse((self.root / "other/policy.json").exists())

    def test_temporal_disagreement_triggers_fallback_even_when_full_shapes_agree(self):
        self.select("shift")
        response = {"status": "needs_native", "reasons": ["direction_changed"]}
        with patch.object(select.temporal, "inspect_run", return_value={}) as inspect:
            with patch.object(select.temporal, "compare", return_value=response):
                result = self.select("other", ["shift"], method="temporal")
        self.assertEqual(inspect.call_count, 2)
        self.assertEqual(result["comparisons"][0]["shape"]["status"], "reuse")
        self.assertEqual(result["comparisons"][0]["temporal"], response)
        self.assertEqual(result["action"], "calibrate")
        with patch.object(select.anchor, "native_once", side_effect=AssertionError("unexpected native execution")):
            self.assertEqual(result["policy_sha256"], self.select("other")["policy_sha256"])

    def test_rejects_foreign_calibration_and_self_reference(self):
        self.select("first")
        shutil.copyfile(self.root / "first/policy.json", self.root / "shift/policy.json")
        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.select("other", ["shift"])
        with self.assertRaisesRegex(ValueError, "distinct earlier"):
            self.select("first", ["first"])

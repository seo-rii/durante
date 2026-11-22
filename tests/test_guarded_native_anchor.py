import gzip
import json
import math
from fractions import Fraction
from pathlib import Path
import platform
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from guarded_native import anchor

STUDY = ROOT / "experiments/guarded_sampler"
SOURCE = ROOT / "experiments/results/gce-guarded-sampler"


class PerExecutableAnchor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        reports = json.loads(gzip.decompress((SOURCE / "dataset.json.gz").read_bytes()))
        cases = {c["case_id"]: c for c in json.loads((STUDY / "cases.json").read_bytes())}
        cls.elf = cls.root / "path_mix"
        with tarfile.open(STUDY / "control/elf.tar.xz") as archive:
            cls.elf.write_bytes(archive.extractfile("bin/path_mix").read())
        cls.elf.chmod(0o700)
        with tarfile.open(SOURCE / "receipts.tar.xz", "r|xz") as tar:
            blobs = {m.name: tar.extractfile(m).read() for m in tar if m.isfile()}
        # Materialize the actual native study's metered receipts in the standalone #82 directory layout.
        for name, modes in (("train", ["calibration"]), ("shift", ["audit_guarded", "audit_fallback"])):
            n = "path_mix/" + name
            run = cls.root / name
            run.mkdir()
            (run / "stdin").write_text(cases[n]["stdin"])
            report = dict(elf_sha256=cases[n]["binary_sha256"], stdin_sha256=cases[n]["stdin_sha256"],
                          authority="diagnostic_only", launches=[])
            for arm in modes:
                row, = [r for r in reports["rows"] if r["arm"] == arm and r["case_id"] == n]
                mode = "guarded" if arm == "audit_guarded" else "full"
                prefix = f"runs/{row['sequence']:03d}/"
                for path in row["receipts"]:
                    dest = run / mode / path.removeprefix(prefix)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(blobs[path])
                launch = {k: row[k] for k in ("exit_code", "launcher_pid", "stdout_sha256")}
                report["launches"].append({**launch, "mode": mode})
                if mode == "guarded":
                    report["decision"] = row["decision"]
            report.update(final_sample="full/sample.json", stdout="full/stdout")
            (run / "result.json").write_text(json.dumps(report))

    @unittest.skipUnless(sys.platform == "linux" and platform.machine() == "x86_64", "native Linux x86-64 ELF")
    def test_actual_native_and_final_full_prediction(self):
        first = self.root / "train"
        output = self.root / "policy.json"
        policy = anchor.calibrate(self.elf, first / "stdin", first, output)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(ValueError, "already exists"):
            anchor.calibrate(self.elf, first / "stdin", first, output)
        with patch.object(anchor, "native_once", side_effect=AssertionError("unexpected native prediction run")):
            result = anchor.predict(output, self.elf, self.root / "shift", 1)
        self.assertEqual((result["observation"]["mode"], result["observation"]["launch_count"]), ("full", 2))
        self.assertNotEqual(result["observation"]["pid"], policy["anchor"]["pid"])
        w = {k: Fraction(v) for k, v in policy["combined_reference_weights"].items()}
        cost = lambda row: sum(row[k] * val for k, val in w.items())
        expected = math.ceil(policy["native"]["cpu_ns"] * cost(result["observation"]) / cost(policy["anchor"]))
        self.assertEqual(result["prediction_ns"], expected)
        self.assertEqual(result["implied_tle"], expected > 1_000_000)
        bad = {**policy, "native": {k: v for k, v in policy["native"].items() if k != "pid"}}
        bad["calibration_sha256"] = anchor.calibration_digest(bad["anchor"], bad["native"])
        incomplete = self.root / "missing-native-pid.json"
        incomplete.write_text(json.dumps(bad))
        with self.assertRaisesRegex(ValueError, "native calibration provenance"):
            anchor.predict(incomplete, self.elf, self.root / "shift")
        policy["anchor"]["instructions"] += 1
        policy["calibration_sha256"] = anchor.calibration_digest(policy["anchor"], policy["native"])
        corrupt = self.root / "wrong-clock.json"
        corrupt.write_text(json.dumps(policy))
        with self.assertRaisesRegex(ValueError, "terminal clock"):
            anchor.predict(corrupt, self.elf, self.root / "shift")

    def test_different_input_or_recovery_cannot_build_first_anchor(self):
        first, later = self.root / "train", self.root / "shift"
        with patch.object(anchor, "native_once", side_effect=AssertionError("unexpected calibration run")):
            for run in (first, later):
                with self.subTest(run=run), self.assertRaisesRegex(ValueError, "initial full measurement"):
                    anchor.calibrate(self.elf, later / "stdin", run, self.root / "wrong.json")

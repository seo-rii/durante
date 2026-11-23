import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from shape_calibration import run


class SelectedInputRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.cases = Path(cls.temp.name)
        result = ROOT / "experiments/results/gce-shape-calibration/output.tar.xz"
        prefix = "output/cases/fenwick/"
        with tarfile.open(result) as archive:
            for item in archive:
                if item.isfile() and item.name.startswith(prefix):
                    rel = Path(item.name[len(prefix):])
                    if not rel.is_absolute() and ".." not in rel.parts:
                        dest = cls.cases / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(archive.extractfile(item).read())
        cls.current = cls.cases / "long"
        cls.policy = cls.cases / "train/policy.json"
        cls.decision = json.loads((cls.current / "selection.json").read_bytes())["temporal"]
        cls.binary_sha = json.loads(cls.policy.read_bytes())["anchor"]["elf_sha256"]

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = Path(tmp.name)
        self.selection = self.out / "selection.json"
        self.selection.write_text(json.dumps(self.decision))
        self.stdin = self.current / "full/stdin"

    def attempt(self, name, policy=None, stdin=None):
        return run.run(self.out, self.selection, self.current, policy or self.policy,
                       self.out / "fixture", stdin or self.stdin, self.out / name, 1000)

    def test_corrupted_bindings_prevent_launch(self):
        with patch.object(run.anchor, "static_elf", return_value=self.binary_sha):
            with patch.object(run.live, "run", side_effect=AssertionError("invalid binding started a job")):
                for name, policy, stdin in (("wrong-input", self.policy, self.cases / "scale/full/stdin"),
                                            ("swapped-policy", self.cases / "large/policy.json", self.stdin)):
                    result = self.attempt(name, policy, stdin)
                    self.assertEqual((result["status"], result["pid"]), ("unavailable", None))
                    self.assertFalse((self.out / name / "live").exists())
                for field, value in (("prediction_ns", 1), ("reference_index", 42), ("current_sample_sha256", "0" * 64)):
                    self.selection.write_text(json.dumps({**self.decision, field: value}))
                    self.assertEqual(self.attempt(field)["status"], "unavailable")

    def test_uses_validated_snapshot_and_never_reprofiles(self):
        requested = self.out / "submitted.in"
        expected = self.stdin.read_bytes()
        requested.write_bytes(expected)
        def launch(payload, policy, elf, frozen_input, out, limit, wall, poll):
            requested.write_bytes(b"changed after validation")
            self.assertEqual(frozen_input.read_bytes(), expected)
            self.assertEqual(policy.read_bytes(), self.policy.read_bytes())
            return {"status": "within_budget", "pid": 123}
        with patch.object(run.anchor, "static_elf", return_value=self.binary_sha), \
             patch.object(run.select_policy, "resolve", side_effect=AssertionError("selection was rerun")), \
             patch.object(run.live, "run", side_effect=launch):
            response = self.attempt("checked", stdin=requested)
        self.assertEqual(response["status"], "within_budget")
        self.assertEqual(response["preflight_prediction_ns"], self.decision["prediction_ns"])
        self.assertEqual(response["selection_sha256"], run.elf_digest(self.selection))

    @unittest.skipUnless(os.getenv("DURANTE_SHAPE_RUNTIME") and os.getenv("DURANTE_SHAPE_BINARIES"),
                         "set the registered #82 runtime and #85 binary directories for real enforcement")
    def test_cli_enforces_real_limits_with_prior_policy(self):
        runtime = Path(os.environ["DURANTE_SHAPE_RUNTIME"])
        elf = Path(os.environ["DURANTE_SHAPE_BINARIES"]) / "fenwick"
        for name, limit, wall, poll, code, observation in (
                ("stop", 1, 120000, 5, 123, "live"), ("pass", 1000, 120000, 5, 0, "terminal"),
                ("late", 1, 120000, 10000, 123, "terminal"), ("wall", 1000, 1, 5, 124, None)):
            with self.subTest(name=name):
                out = self.out / name
                command = [sys.executable, "-B", run.__file__, "--payload", runtime, "--selection", self.selection,
                           "--run", self.current, "--policy", self.policy, "--stdin", self.stdin, "--elf", elf,
                           "--out", out, "--limit-ms", str(limit), "--timeout-ms", str(wall), "--poll-ms", str(poll)]
                trial = subprocess.run(command, capture_output=True, timeout=30)
                result = json.loads(trial.stdout)
                self.assertEqual(trial.returncode, code, result)
                self.assertEqual(result["observation"], observation)
                self.assertTrue(result["pid"] > 0)
                inner = json.loads((out / "live/result.json").read_bytes())
                self.assertEqual((result["status"], result["pid"]), (inner["status"], inner["pid"]))

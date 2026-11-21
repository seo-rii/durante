from fractions import Fraction
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "experiments/rust_operand_bytes"
spec = importlib.util.spec_from_file_location("evaluate_rust_operand_bytes", STUDY / "evaluate.py")
evaluate = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", sys.path.copy()):
    spec.loader.exec_module(evaluate)


class OperandByteFitTests(unittest.TestCase):
    def test_protocol_covers_the_original_executables_and_inputs(self):
        prior = ROOT / "experiments/results/gce-rust-instruction-mix"
        plan = json.loads((STUDY / "protocol.json").read_bytes())
        expected = {(case["workload_id"], case["scale"]): case for case in plan["cases"]}
        observed = set()
        for path, digest in zip((prior / "dataset.json.gz", prior / "holdout/dataset.json.gz"),
                                plan["prior_dataset_sha256"], strict=True):
            raw = gzip.decompress(path.read_bytes())
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            for row in json.loads(raw)["samples"]:
                key = row["workload_id"], row["scale"]
                observed.add(key)
                self.assertEqual({k: row[k] for k in expected[key]}, expected[key])
        self.assertEqual(observed, set(expected))

    def test_archived_experiment_replays(self):
        results = ROOT / "experiments/results/gce-rust-operand-bytes"
        dataset = results / "dataset.json.gz"
        for script, args in (
            ("evaluate.py", ["--verify", results / "evaluation.json"]),
            ("verify_receipts.py", [results / "receipts.tar.gz"]),
            ("verify_placement.py", [results / "placement.json.gz", "--platform", results / "platform.json"]),
        ):
            with self.subTest(script=script):
                run = subprocess.run([sys.executable, STUDY / script, dataset, *args], cwd=ROOT,
                                     text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
                self.assertEqual(run.returncode, 0, run.stdout)
        limits = subprocess.check_output(
            [sys.executable, STUDY / "model_limit.py", results / "evaluation.json"], cwd=ROOT, timeout=60
        )
        self.assertEqual(json.loads(limits), json.loads((results / "model-limit.json").read_bytes()))

    def test_two_feature_fit_keeps_transfer_programs_out_of_all_fits(self):
        plan = json.loads((STUDY / "protocol.json").read_bytes())
        points, targets = {}, {}
        for i, item in enumerate(plan["cases"], 1):
            key = item["workload_id"], item["scale"]
            counts = {"latency_cycles": Fraction(33*i, 16), "memory_bytes": 5*i*i,
                      "instructions": 4*i, "rep_bytes": 0}
            points[key] = {"features": [counts]*3}
            t = 3*counts["latency_cycles"] + 7*counts["memory_bytes"]
            targets[key] = 2*t if key[0] in plan["transfer"] else t
        fit = evaluate.score_models(points, plan, targets)["latency_memory"]
        self.assertEqual(fit["coefficients_ns"], {"latency_cycles": "3", "memory_bytes": "7"})
        self.assertEqual(fit["validation"]["passed"], 9)
        self.assertEqual(fit["transfer"]["passed"], 0)
        self.assertEqual(fit["transfer"]["worst_signed_error_ppm"], -500_000)
        self.assertEqual(fit["leave_one_program_out"]["passed"], 15)


if __name__ == "__main__":
    unittest.main()

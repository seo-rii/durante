"""Offline accuracy and identifiability gates for the fixed load-model experiment."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "experiments" / "load_model.py"
spec = importlib.util.spec_from_file_location("load_model", SCRIPT)
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)


def collection(load_weight=3):
    vectors = {"integer": (1000, 0, 0), "rep_copy": (1000, 400, 100), "sort": (1000, 0, 500),
               "branch": (1200, 0, 60), "rep_fill": (500, 700, 10),
               "lower_bound": (800, 4, 320), "window_sum": (450, 8, 80)}
    data = {"schema": 1, "complete": True, "cpuid_profile": "x86-64-v2", "repetitions": 3, "cases": []}
    for family, (i, r, l) in vectors.items():
        for scale in (1, 2, 4):
            ns = (i + 2*r + load_weight*l) * scale
            rows = [{"repetition": k, "instructions": i*scale, "rep_bytes": r*scale,
                     "load_instructions": l*scale, "native_before_ns": ns-1, "native_after_ns": ns+1}
                    for k in (1, 2, 3)]
            data["cases"].append({"family": family, "scale": scale, "repetitions": rows})
    return data


class LoadModelTests(unittest.TestCase):
    def test_nested_fits_and_prospectively_held_out_families(self):
        data = collection()
        report = model.evaluate(data)
        full = report["models"]["with_loads"]
        weights = {"instructions": 1, "rep_bytes": 2, "load_instructions": 3}
        self.assertEqual(full["diagnostic_ns_per_count"], weights)
        self.assertTrue(full["identified"])
        self.assertTrue(full["accuracy_gate_pass"])
        self.assertEqual(set(report["families"]["training"]), {"integer", "rep_copy", "sort"})
        self.assertEqual(set(report["families"]["unseen"]), {"lower_bound", "window_sum"})
        for group in ("historical", "unseen"):
            self.assertEqual(full["groups"][group], {"passed": 18, "total": 18, "pass": True,
                                                    "max_absolute_relative_error": 0})
        self.assertEqual(full["groups"]["all"]["total"], 36)
        self.assertLess(report["models"]["baseline"]["groups"]["all"]["passed"], 36)

        # A different held-out native reference changes only evaluation.
        for case in data["cases"]:
            if case["family"] == "window_sum":
                for row in case["repetitions"]:
                    row["native_before_ns"] *= 10
                    row["native_after_ns"] *= 10
        updated = model.evaluate(data)
        for label, fitted in report["models"].items():
            self.assertEqual(updated["models"][label]["diagnostic_ns_per_count"],
                             fitted["diagnostic_ns_per_count"])
        self.assertFalse(updated["models"]["with_loads"]["accuracy_gate_pass"])

    def test_historical_failures_do_not_change_the_prospective_accuracy_gate(self):
        data = collection()
        for case in data["cases"]:
            if case["family"] == "rep_fill":
                for row in case["repetitions"]:
                    row["native_before_ns"] *= 10
                    row["native_after_ns"] *= 10
        candidate = model.evaluate(data)["models"]["with_loads"]
        self.assertFalse(candidate["groups"]["all"]["pass"])
        self.assertFalse(candidate["groups"]["historical"]["pass"])
        self.assertTrue(candidate["groups"]["unseen"]["pass"])
        self.assertTrue(candidate["accuracy_gate_pass"])

    def test_zero_load_coefficient_is_a_valid_nonnegative_fit(self):
        report = model.evaluate(collection(load_weight=0))
        self.assertTrue(report["models"]["baseline"]["accuracy_gate_pass"])
        candidate = report["models"]["with_loads"]
        self.assertTrue(candidate["accuracy_gate_pass"])
        self.assertEqual(candidate["diagnostic_ns_per_count"]["load_instructions"], 0)
        # A negative unconstrained slope must also project to the boundary.
        constrained = model.evaluate(collection(load_weight=-1))["models"]["with_loads"]
        self.assertTrue(constrained["identified"])
        self.assertEqual(constrained["diagnostic_ns_per_count"]["load_instructions"], 0)

    def test_rank_and_near_collinearity_are_checked_on_training_only(self):
        for defect in ("third column rank deficient", "third column near collinear", "baseline rank deficient"):
            data = collection()
            for case in data["cases"]:
                if case["family"] not in ("integer", "rep_copy", "sort"):
                    continue
                for row in case["repetitions"]:
                    if defect == "baseline rank deficient":
                        row["rep_bytes"] = row["instructions"]
                    elif defect == "third column near collinear":
                        row["instructions"] *= 2**30
                        row["load_instructions"] = row["instructions"]//2 + int(case["family"] == "sort")
                    else:
                        row["load_instructions"] = row["instructions"]//2
            with self.subTest(defect=defect):
                models = model.evaluate(data)["models"]
                self.assertEqual(models["baseline"]["identified"], defect != "baseline rank deficient")
                self.assertFalse(models["with_loads"]["identified"])
                self.assertFalse(models["with_loads"]["accuracy_gate_pass"])
                self.assertNotIn("diagnostic_ns_per_count", models["with_loads"])

    def test_native_median_spreads_and_repetition_gate_use_exact_arithmetic(self):
        data = collection()
        case = next(c for c in data["cases"] if c["family"] == "lower_bound" and c["scale"] == 1)
        n = 2**54
        for row, work in zip(case["repetitions"], (4*n, 6*n, 6*n+1)):
            row.update(instructions=work, rep_bytes=0, load_instructions=0,
                       native_before_ns=5*n-1, native_after_ns=5*n)
        report = model.evaluate(data)
        summary = report["cases"]["lower_bound-1"]
        self.assertEqual(summary["native_ns"], {
            "min": 5*n-1, "max": 5*n, "median": 5*n, "relative_spread": 1/(5*n)})
        self.assertEqual(summary["counters"]["instructions"]["median"], 6*n)
        predictions = report["models"]["with_loads"]["cases"]["lower_bound-1"]["predictions"]
        self.assertEqual([r["within_20_percent"] for r in predictions], [True, True, False])
        self.assertEqual(report["models"]["with_loads"]["groups"]["all"]["passed"], 35)
        self.assertFalse(report["models"]["with_loads"]["accuracy_gate_pass"])
        self.assertEqual(model.distribution([0, 0, 0])["relative_spread"], 0)
        self.assertIsNone(model.distribution([0, 0, 1])["relative_spread"])

    def test_rejects_missing_duplicates_wrong_profile_and_noninteger_measurements(self):
        base = collection()
        for field, bad in [("schema", True), ("complete", 1), ("cpuid_profile", "host"),
                           ("repetitions", True)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                model.evaluate({**base, field: bad})
        with self.assertRaises(ValueError):
            model.evaluate({**base, "cases": base["cases"][:-1]})
        with self.assertRaises(ValueError):
            model.evaluate({**base, "cases": base["cases"] + [base["cases"][0]]})
        for field, bad in [("repetition", True), ("repetition", 2), ("native_after_ns", 0),
                           ("instructions", False), ("rep_bytes", -1), ("load_instructions", 1001),
                           ("native_before_ns", 2**64)]:
            data = copy.deepcopy(base)
            data["cases"][0]["repetitions"][0][field] = bad
            with self.subTest(field=field), self.assertRaises(ValueError):
                model.evaluate(data)

    def test_cli_verifies_manifest_and_reports_paired_overhead(self):
        data = collection()
        corpus_bytes = SCRIPT.with_name("load_corpus.json").read_bytes()
        corpus = json.loads(corpus_bytes)
        data.update(corpus_sha256=hashlib.sha256(corpus_bytes).hexdigest(),
                    compiler_image=corpus["compiler_image"])
        for case in data["cases"]:
            fixture = corpus["families"][case["family"]]
            expected = next(c for c in fixture["cases"] if c["scale"] == case["scale"])
            case["binary_sha256"] = fixture["binary_sha256"]
            case["input_sha256"] = hashlib.sha256(expected["stdin"].encode()).hexdigest()
            # Median of ratios = 3/2, ratio of medians = 2.
            for row, meter, probe in zip(case["repetitions"], (200, 1000, 100), (400, 1000, 150)):
                row.update(meter_cpu_ns=meter, probe_cpu_ns=probe)
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "collection.json", Path(tmp) / "evaluation.json"
            source.write_text(json.dumps(data))
            cmd = [sys.executable, SCRIPT, "--input", source, "--output", output]
            subprocess.run(cmd, check=True, capture_output=True)
            receipt = output.read_bytes()
            result = json.loads(receipt)
            self.assertEqual(result["input_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            expected = {"pairs": 63, "min": 1, "median": 1.5, "max": 2}
            measured = result["descriptive_probe_to_meter_cpu_ratio"]
            self.assertEqual(measured["all"], expected)
            self.assertEqual(measured["cases"]["lower_bound-4"], {**expected, "pairs": 3})
            self.assertNotEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
            self.assertEqual(output.read_bytes(), receipt)
        for key in ("corpus_sha256", "compiler_image"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                model.check_corpus({**data, key: "wrong"}, corpus_bytes)
        for key in ("binary_sha256", "input_sha256"):
            tampered = copy.deepcopy(data)
            tampered["cases"][4][key] = "wrong"
            with self.subTest(key=key), self.assertRaises(ValueError):
                model.check_corpus(tampered, corpus_bytes)
        for key in ("meter_cpu_ns", "probe_cpu_ns"):
            cases = copy.deepcopy(data["cases"])
            cases[0]["repetitions"][0][key] = 0
            with self.subTest(key=key), self.assertRaises(ValueError):
                model.overhead(cases)


if __name__ == "__main__":
    unittest.main()

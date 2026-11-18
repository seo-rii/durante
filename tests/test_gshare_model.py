"""Prospective screening of the four-counter CPP20 model."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments"
with patch.object(sys, "path", [str(EXPERIMENTS), *sys.path]):
    import gshare_model


def collection():
    # Mispredictions vary independently of loads in the five training families.
    vectors = {"integer": (1000, 0, 0, 0), "rep_copy": (1000, 400, 100, 1),
               "sort": (1000, 0, 500, 80), "lower_bound": (800, 0, 250, 120),
               "window_sum": (450, 8, 80, 2), "branch": (1200, 0, 60, 340),
               "rep_fill": (500, 700, 10, 1), "merge_streams": (1200, 4, 150, 450),
               "hash_probe": (500, 0, 50, 140)}
    data = {"schema": 1, "complete": True, "cpuid_profile": "x86-64-v2", "repetitions": 3,
            "gshare_profile": "gshare-14bit-pcshift1-v1", "personality_hex": "00040000",
            "probe_options": ["-bpred"], "counter_integrity": "application_mutable", "cases": []}
    for family, (instructions, rep, loads, misses) in vectors.items():
        for scale in (1, 2, 4):
            native = (instructions + 2 * rep + 3 * loads + 12 * misses) * scale
            rows = [{"repetition": r, "native_before_ns": native - 1, "native_after_ns": native + 1,
                     "instructions": instructions * scale, "rep_bytes": rep * scale,
                     "load_instructions": loads * scale, "gshare_mispredicts": misses * scale,
                     "conditional_branches": (misses + 10) * scale, "probe_cpu_ns": 150,
                     "meter_cpu_ns": 100} for r in (1, 2, 3)]
            data["cases"].append({"family": family, "scale": scale, "repetitions": rows})
    return data


class GshareModelTests(unittest.TestCase):
    def test_fit_uses_five_previously_seen_families_and_only_four_candidate_features(self):
        data = collection()
        result = gshare_model.evaluate(data)
        self.assertEqual(set(result["families"]["training"]),
                         {"integer", "rep_copy", "sort", "lower_bound", "window_sum"})
        self.assertEqual(set(result["families"]["unseen"]), {"merge_streams", "hash_probe"})
        model = result["models"]["with_gshare"]
        self.assertTrue(model["identified"])
        self.assertEqual(model["diagnostic_ns_per_count"], {
            "instructions": 1, "rep_bytes": 2, "load_instructions": 3, "gshare_mispredicts": 12})
        self.assertEqual(result["models"]["baseline"]["features"], ("instructions", "rep_bytes", "load_instructions"))
        self.assertTrue(model["accuracy_gate_pass"])
        self.assertEqual(model["groups"]["unseen"]["passed"], 18)
        self.assertEqual(model["groups"]["all"]["total"], 36)
        self.assertFalse(result["production_qualified"])

        for case in data["cases"]:
            if case["family"] in ("hash_probe", "branch"):
                for row in case["repetitions"]:
                    row["native_before_ns"] *= 2
                    row["native_after_ns"] *= 2
        changed = gshare_model.evaluate(data)
        for label, fit in result["models"].items():
            self.assertEqual(fit["diagnostic_ns_per_count"], changed["models"][label]["diagnostic_ns_per_count"])
        self.assertEqual(changed["models"]["with_gshare"]["groups"]["unseen"]["passed"], 9)
        self.assertFalse(changed["models"]["with_gshare"]["accuracy_gate_pass"])

    def test_new_column_must_be_identifiable_on_training_data(self):
        data = collection()
        for case in data["cases"]:
            if case["family"] in ("integer", "rep_copy", "sort", "lower_bound", "window_sum"):
                for row in case["repetitions"]:
                    row["gshare_mispredicts"] = row["load_instructions"]
                    row["conditional_branches"] = row["instructions"]
        models = gshare_model.evaluate(data)["models"]
        self.assertTrue(models["baseline"]["identified"])
        self.assertFalse(models["with_gshare"]["identified"])
        self.assertFalse(models["with_gshare"]["accuracy_gate_pass"])

    def test_rejects_wrong_predictor_layout_and_impossible_counters(self):
        original = collection()
        for key, value in (("gshare_profile", "another"), ("personality_hex", "00000000"),
                           ("personality_hex", None), ("personality_hex", "-0040000"),
                           ("probe_options", ["-bpred", "-load_pattern"]), ("counter_integrity", "trusted")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                gshare_model.evaluate({**original, key: value})
        for change in ({"gshare_mispredicts": True}, {"gshare_mispredicts": -1},
                       {"gshare_mispredicts": 11}, {"conditional_branches": 1001},
                       {"conditional_branches": True}):
            data = copy.deepcopy(original)
            data["cases"][0]["repetitions"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                gshare_model.evaluate(data)
        with self.assertRaises(ValueError):
            gshare_model.evaluate({**original, "cases": original["cases"][:-1]})

    def test_recorded_integer_and_window_rule_out_nonnegative_reweighting(self):
        raw = json.loads((EXPERIMENTS / "results/cpp20-gshare-model.json").read_bytes())
        result = gshare_model.evaluate(raw)
        integer = result["cases"]["integer-4"]
        window = result["cases"]["window_sum-4"]
        # Even the largest integer count is at most 1.03 times the smallest
        # window count in every dimension, so every nonnegative sum obeys it.
        for key in gshare_model.MODELS["with_gshare"]:
            self.assertLessEqual(100 * integer["counters"][key]["max"],
                                 103 * window["counters"][key]["min"])
        # Even the most favorable times cannot meet ±20% for both workloads.
        self.assertGreater(100 * 80 * integer["native_ns"]["min"],
                           103 * 120 * window["native_ns"]["max"])

    def test_public_cli_binds_both_corpora_and_reports_overhead_for_81_pairs(self):
        data = collection()
        base_bytes = (EXPERIMENTS / "load_corpus.json").read_bytes()
        branch_bytes = (EXPERIMENTS / "branch_corpus.json").read_bytes()
        base, branch = json.loads(base_bytes), json.loads(branch_bytes)
        fixtures = {**base["families"], **branch["families"]}
        reference = (EXPERIMENTS / "results/cpp20-load-model.json").read_bytes()
        data.update(reference_sha256=hashlib.sha256(reference).hexdigest(),
                    tools_sha256=json.loads(reference)["tools_sha256"],
                    corpus_sha256=hashlib.sha256(branch_bytes).hexdigest(),
                    base_corpus_sha256=hashlib.sha256(base_bytes).hexdigest(), compiler_image=base["compiler_image"])
        for case in data["cases"]:
            fixture = fixtures[case["family"]]
            stdin = next(c["stdin"] for c in fixture["cases"] if c["scale"] == case["scale"])
            case.update(binary_sha256=fixture["binary_sha256"], input_sha256=hashlib.sha256(stdin.encode()).hexdigest())
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "collection.json", Path(tmp) / "result.json"
            cmd = [sys.executable, EXPERIMENTS / "gshare_model.py", "--input", source, "--output", output]
            source.write_text(json.dumps(data))
            subprocess.run(cmd, check=True, capture_output=True)
            result = json.loads(output.read_bytes())
            self.assertEqual(result["input_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(result["descriptive_probe_to_meter_cpu_ratio"]["all"],
                             {"pairs": 81, "min": 1.5, "median": 1.5, "max": 1.5})
            output.unlink()
            for key in ("reference_sha256", "corpus_sha256", "base_corpus_sha256", "compiler_image"):
                source.write_text(json.dumps({**data, key: "stale"}))
                with self.subTest(key=key):
                    self.assertNotEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
                    self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()

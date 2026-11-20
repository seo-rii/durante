"""Reconcile the sealed CPP20 memory-model predictions with the published Cloud Run receipts."""
from datetime import datetime
from fractions import Fraction
from hashlib import sha256
from importlib.util import find_spec
import json
from math import ceil, floor
from pathlib import Path
from statistics import median
import subprocess
import sys
import tempfile
import unittest

EXP = Path(__file__).resolve().parents[1] / "experiments"
DATA = EXP / "results/cpp20-v8-memory"
sys.path.insert(0, str(EXP))
from memory_cost_capacity import normalized_rows, verify
from memory_cost_model import DIGESTS, EXTERNAL, FIELDS, LABELS, MANIFEST, MODELS, TRAINING


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class MemoryCostReceipts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.features = read(DATA / "features.json")
        cls.pred = read(DATA / "predictions.json")
        cls.cloud = read(DATA / "cloud.json")
        cls.anchors = read(DATA / "anchors.json")
        cls.labels = read(LABELS)["training"]

    def test_seals_and_sequence(self):
        pred, cloud, anchors = self.pred, self.cloud, self.anchors
        self.assertEqual(digest(DATA / "predictions.json"),
                         "e26b1001a473e22d72ac9dbad5fac463a9063e0e6a62eb488190ffcdd190efcd")
        self.assertEqual(cloud["predictions_sha256"], digest(DATA / "predictions.json"))
        self.assertEqual(pred["features_sha256"], digest(DATA / "features.json"))
        self.assertEqual(pred["labels_sha256"], digest(LABELS))
        self.assertEqual(pred["manifest_sha256"], digest(MANIFEST))
        self.assertEqual(cloud["manifest_sha256"], pred["manifest_sha256"])
        self.assertEqual(cloud["model_protocol_sha256"], digest(DATA / "model-protocol.json"))
        self.assertEqual(self.features["provenance"]["protocol_sha256"], cloud["model_protocol_sha256"])
        self.assertEqual(cloud["protocol_sha256"], digest(DATA / "boundary-protocol.json"))
        self.assertEqual(cloud["anchor_protocol_sha256"], digest(DATA / "anchor-protocol.json"))
        self.assertEqual(anchors["protocol_sha256"], cloud["anchor_protocol_sha256"])
        self.assertEqual(cloud["anchors_sha256"], digest(DATA / "anchors.json"))
        self.assertEqual(anchors["historical_training_sha256"], pred["labels_sha256"])
        manifest = read(MANIFEST)
        times = [manifest["predeclared_utc"], manifest["frozen_utc"], cloud["gate_verified_utc"],
                 cloud["started_utc"], cloud["completed_utc"], anchors["completed_utc"]]
        for first, second in zip(times, times[1:]):
            self.assertLess(stamp(first), stamp(second))

    def test_sampled_inputs_and_integer_predictions(self):
        self.assertEqual(set(self.features["training"]), TRAINING)
        self.assertEqual(set(self.features["external"]), EXTERNAL)
        self.assertEqual(set(self.pred["external"]), EXTERNAL)
        manifest = read(MANIFEST)["cases"]
        self.assertEqual(set(manifest), EXTERNAL)
        for group in ("training", "external"):
            for name, case in self.features[group].items():
                with self.subTest(case=name):
                    rows = case["counters"]
                    self.assertEqual(len(rows), 3)
                    for row in rows:
                        self.assertEqual(set(row), set(FIELDS))
                        self.assertEqual(row, rows[0])
                        self.assertTrue(all(type(v) is int and v >= 0 for v in row.values()))
                    if group == "training":
                        for key in DIGESTS:
                            self.assertEqual(case[key], self.labels[name][key])
                        continue
                    prospective = self.pred["external"][name]
                    fixture = MANIFEST.parent / name
                    for file, key in (("main.cpp", "source_sha256"), ("in", "stdin_sha256"), ("oracle", "stdout_sha256")):
                        self.assertEqual(digest(fixture / file), manifest[name][key])
                    for key, declared in zip(DIGESTS, ("elf_sha256", "stdin_sha256", "stdout_sha256")):
                        self.assertEqual(case[key], manifest[name][declared])
                        self.assertEqual(prospective[key], self.cloud["cases"][name][key])
                        self.assertEqual(prospective[key], case[key])
                    for model, fields in MODELS.items():
                        weights = self.pred["models"][model]["weights"]
                        self.assertEqual(set(weights), set(fields))
                        ns = [ceil(Fraction(sum(r[k] * weights[k] for k in fields), self.pred["denominator"]))
                              for r in rows]
                        self.assertEqual(prospective["predictions_ns"][model], ns)
                        self.assertEqual(prospective[model + "_median_ns"], median(ns))

    @unittest.skipUnless(find_spec("numpy") and find_spec("scipy"), "fit uses NumPy and SciPy")
    def test_refit_from_published_data(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "predictions.json"
            subprocess.run([sys.executable, "-B", str(EXP / "memory_cost_model.py"),
                            str(DATA / "features.json"), "--output", str(out)], check=True)
            self.assertEqual(out.read_bytes(), (DATA / "predictions.json").read_bytes())

    def test_capacity_certificates(self):
        capacity = read(DATA / "capacity.json")
        self.assertEqual(capacity["features_sha256"], self.pred["features_sha256"])
        self.assertEqual(capacity["labels_sha256"], self.pred["labels_sha256"])
        self.assertEqual(capacity["case_order"], sorted(TRAINING))
        for model, fields in MODELS.items():
            bounds = capacity["models"][model]
            self.assertEqual(bounds["fields"], list(fields))
            for target in ("median", "favorable_range"):
                observations = []
                for name in capacity["case_order"]:
                    native = self.labels[name]["native_ns"]
                    low = high = median(map(Fraction, native))
                    if target == "favorable_range":
                        low, high = min(native), max(native)
                    for row in self.features["training"][name]["counters"]:
                        observations.append(([row[k] for k in fields], low, high))
                proof = bounds[target]
                low, high = verify(*normalized_rows(observations), proof["certificate"])
                self.assertEqual(proof["lower_error_ppb"], floor(low * 10**9))
                self.assertEqual(proof["upper_error_ppb"], ceil(high * 10**9))

    def test_requests_and_actual_live_cutoff(self):
        cloud, anchors = self.cloud, self.anchors
        place = read(DATA / "placement.json")
        self.assertEqual(place["revision"], cloud["revision"])
        self.assertEqual(anchors["revision"], cloud["revision"])
        self.assertEqual(anchors["image"], cloud["image"])
        self.assertTrue(place["all_execution_logs_recorded"])
        self.assertEqual(place["requests_per_recorded_instance"], [84])
        self.assertEqual((place["distinct_recorded_instances"], place["unknown_requests"]), (1, 0))
        installation = cloud["v8_installation"]
        for local, hosted in (("runtime", "release_core_sha256"), ("meter", "client_sha256"), ("drrun", "launcher_sha256")):
            self.assertEqual(self.features["provenance"]["local_sha256"][local], installation[hosted])
        historical = read(DATA / "boundary-protocol.json")["historical_model"]
        weights = [historical[k + "_ns_numerator"] for k in ("instruction", "movs_byte", "stos_byte")]
        denominator = historical["ns_denominator"]
        observed = 0
        cutoffs = []
        self.assertEqual(set(cloud["cases"]), EXTERNAL)
        for name, case in cloud["cases"].items():
            self.assertEqual([r["repetition"] for r in case["rounds"]], [1, 2, 3])
            for row in case["rounds"]:
                for key in ("native_before", "native_after", "v8_sample"):
                    self.assertEqual(row[key]["status"], "Accepted")
                    observed += 1
            for row in case["boundaries"]:
                self.assertEqual(len(row["native_samples"]), 3)
                self.assertEqual([x["status"] for x in row["native_samples"]], row["native"])
                observed += len(row["native_samples"])
                sample = row["v8_sample"]
                if sample is None:
                    self.assertIsNone(row["v8"])
                    continue
                observed += 1
                self.assertEqual(sample["status"], row["v8"])
                self.assertEqual(sample["budget_ns"], row["limit_ms"] * 1_000_000)
                self.assertEqual(sample["progress_overflow"], 0)
                work = sum(sample["progress"][k] * w for k, w in zip(("instructions", "movs_bytes", "stos_bytes"), weights))
                if sample["status"] == "Time Limit Exceeded":
                    self.assertEqual(sample["termination"], "durante_work_clock")
                    self.assertIsNone(sample["estimated_ns"])
                    self.assertGreater(work, sample["budget_ns"] * denominator)
                    cutoffs.append((name, row["limit_ms"]))
                else:
                    self.assertEqual(sample["status"], "Accepted")
                    self.assertEqual(sample["estimated_ns"], ceil(Fraction(work, denominator)))
        self.assertEqual(cutoffs, [("kmp-match", 57)])
        self.assertEqual(observed, cloud["prospective_http_responses"])
        self.assertEqual(observed, 69)
        anchor_count = 0
        for family, case in anchors["cases"].items():
            old = self.labels[family + "-2"]
            self.assertEqual(case["old_native_ns"], old["native_ns"])
            for key in DIGESTS:
                self.assertEqual(case[key], old[key])
            new = [r["cpu_ns"] for r in case["new_samples"] if r["status"] == "Accepted"]
            self.assertEqual(case["new_native_ns"], new)
            self.assertEqual(len(new), 3)
            anchor_count += len(new)
        self.assertEqual(anchor_count, cloud["anchor_http_responses"])
        self.assertEqual(anchor_count, anchors["http_responses"])
        self.assertEqual(observed + anchor_count, cloud["http_responses"])
        self.assertEqual(cloud["http_responses"], place["successful_request_logs"])
        self.assertEqual(place["successful_request_logs"], place["expected_http_executions"])

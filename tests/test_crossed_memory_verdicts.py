"""Reconcile the two Cloud Run collection stages and their actual CPU-limit verdicts."""
from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sys
import unittest

EXP = Path(__file__).resolve().parents[1] / "experiments"
DATA = EXP / "results/cpp20-crossed-memory"
sys.path.insert(0, str(EXP))
from crossed_memory_model import CALIBRATION, EXTERNAL
from memory_cost_model import CANDIDATE
from score_memory_cost import AC, TLE, native_verdict, score


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


class CrossedMemoryVerdicts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cal = read(DATA / "calibration.json")
        cls.cloud = read(DATA / "cloud.json")
        cls.rules = read(DATA / "request-protocol.json")
        cls.placement = read(DATA / "placement.json")
        cls.cleanup = read(DATA / "cleanup.json")

    def test_corpus_calibration_and_predictions_were_sealed_before_their_stages(self):
        manifest_path = EXP / "crossed_memory_corpus/manifest.json"
        manifest = read(manifest_path)
        pred = read(DATA / "predictions.json")
        self.assertEqual(digest(DATA / "predictions.json"),
                         "ca98b0fc761fe2dd020163e06534bb3a893e22a381a7f42d33d4f0ad2aab4478")
        self.assertEqual(self.cloud["predictions_sha256"], digest(DATA / "predictions.json"))
        self.assertEqual(pred["calibration_sha256"], digest(DATA / "calibration.json"))
        self.assertEqual(self.cloud["calibration_sha256"], pred["calibration_sha256"])
        self.assertEqual(pred["manifest_sha256"], digest(manifest_path))
        self.assertEqual(read(DATA / "protocol.json")["cloud_request_protocol_sha256"],
                         digest(DATA / "request-protocol.json"))
        for stage, cohort, identities in ((self.cal, "calibration", CALIBRATION), (self.cloud, "external", EXTERNAL)):
            self.assertEqual(stage["stage"], cohort)
            self.assertEqual(set(stage["cases"]), identities)
            self.assertEqual(set(self.rules[cohort]), identities)
            self.assertEqual(stage["manifest_sha256"], pred["manifest_sha256"])
            self.assertEqual(stage["protocol_sha256"], digest(DATA / "request-protocol.json"))
            self.assertEqual(stage["experiment_protocol_sha256"], digest(DATA / "protocol.json"))
            for name, case in stage["cases"].items():
                fixture = manifest["cases"][name]
                self.assertEqual((case["elf_sha256"], case["input_sha256"], case["output_sha256"]),
                                 (fixture["elf_sha256"], fixture["stdin_sha256"], fixture["stdout_sha256"]))
        dates = [manifest["predeclared_utc"], manifest["frozen_utc"], self.cal["calibration_gate_verified_utc"],
                 self.cal["started_utc"], self.cal["completed_utc"], self.cloud["gate_verified_utc"],
                 self.cloud["started_utc"], self.cloud["completed_utc"], self.cleanup["verified_utc"]]
        stamps = [datetime.fromisoformat(s.replace("Z", "+00:00")) for s in dates]
        for left, right in zip(stamps, stamps[1:]):
            self.assertLess(left, right)

    def check_live(self, sample, ms, binding):
        limits = self.rules["triplets"]
        self.assertEqual(sample["clock_model_binding"], binding)
        self.assertEqual(sample["verdict_source"], "durante_judge")
        self.assertEqual(sample["authority"], "diagnostic_only")
        self.assertEqual(sample["raw_limits"], {"cpu_time_ms": limits["outer_live_cpu_ms"],
                                                "wall_time_ms": limits["outer_live_wall_ms"],
                                                "memory_mb": limits["memory_mb"]})
        self.assertEqual(sample["nominal_limits"], {"time_ms": ms, "wall_time_ms": limits["nominal_wall_ms"],
                                                    "memory_mb": limits["memory_mb"]})
        self.assertLess(ms, limits["outer_live_cpu_ms"])
        self.assertLess(limits["nominal_wall_ms"], limits["outer_live_wall_ms"])
        self.assertTrue(0 < sample["cpu_ns"] < limits["outer_live_cpu_ms"] * 1_000_000)
        self.assertTrue(0 <= sample["wall_ms"] < limits["outer_live_wall_ms"])
        self.assertEqual(sample["budget_ns"], ms * 1_000_000)
        self.assertEqual(sample["progress_overflow"], 0)
        progress = sample["progress"]
        self.assertEqual(set(progress), {"instructions", "movs_bytes", "stos_bytes"})
        self.assertTrue(all(type(n) is int and 0 <= n < 2**64 for n in progress.values()))
        model = self.rules["historical_model"]
        total = sum(progress[k] * model[w + "_ns_numerator"] for k, w in
                    (("instructions", "instruction"), ("movs_bytes", "movs_byte"), ("stos_bytes", "stos_byte")))
        denominator = model["ns_denominator"]
        crossed = total > sample["budget_ns"] * denominator
        self.assertEqual(sample["status"], TLE if crossed else AC)
        if crossed:
            self.assertEqual((sample["termination"], sample["state"], sample["quality"]),
                             ("durante_work_clock", "budget_exceeded", "incomplete"))
            self.assertFalse(sample["work_complete"])
            for key in ("estimated_ns", "output_matches", "exit_code"):
                self.assertIsNone(sample[key])
        else:
            self.assertEqual((sample["termination"], sample["state"], sample["quality"]),
                             ("stdout", "complete", "uninterrupted"))
            self.assertTrue(sample["work_complete"])
            self.assertIs(sample["output_matches"], True)
            self.assertEqual((sample["exit_code"], sample["unsupported_rep_entries"]), (0, 0))
            self.assertEqual(sample["estimated_ns"], (total + denominator - 1) // denominator)

    def test_every_native_and_live_sample_reconciles_with_its_limits_and_placement(self):
        for k in ("revision", "image", "v8_policy", "v8_runtime_options_id", "v8_installation", "deployed"):
            self.assertEqual(self.cal[k], self.cloud[k])
        policy = self.cloud["v8_policy"]
        for k, value in self.rules["historical_model"].items():
            if k != "purpose":
                self.assertEqual(policy[k], value)
        # The Go binding serializes these fields in TimePolicy declaration order.
        fields = ("method", "model_id", "model_version", "reference_id", "runtime_id", "family", "engine_profile",
                  "dispatch_profile", "cpuid_profile", "instruction_ns_numerator", "movs_byte_ns_numerator",
                  "stos_byte_ns_numerator", "ns_denominator")
        bound = {"Policy": {k: policy[k] for k in fields}, "RuntimeOptionsID": self.cloud["v8_runtime_options_id"],
                 "RuntimeDeterminism": ""}
        binding = "work-clock-v1:sha256:" + sha256(json.dumps(bound, separators=(",", ":")).encode()).hexdigest()
        total = Counter()
        for stage in (self.cal, self.cloud):
            verdicts = Counter()
            for name, case in stage["cases"].items():
                with self.subTest(case=name):
                    full_limit = self.rules["triplets"]["nominal_cpu_ms"]
                    self.assertEqual([r["repetition"] for r in case["rounds"]], [1, 2, 3])
                    for r in case["rounds"]:
                        for side in ("native_before", "native_after"):
                            self.assertEqual(native_verdict(r[side], full_limit), AC)
                            self.assertEqual(r[side]["cpu_ns"], r[side + "_ns"])
                        live = r["v8_sample"]
                        self.check_live(live, full_limit, binding)
                        self.assertEqual(live["status"], AC)
                        self.assertEqual(live["progress"], {k: r[k] for k in live["progress"]})
                        self.assertEqual((r["v8_cpu_ns"], r["predicted_ns"]), (live["cpu_ns"], live["estimated_ns"]))
                        verdicts[AC] += 3
                    for b in case.get("boundaries", []):
                        verdicts.update(native_verdict(s, b["limit_ms"]) for s in b["native_samples"])
                        if b["v8_sample"] is not None:
                            self.check_live(b["v8_sample"], b["limit_ms"], binding)
                            verdicts[b["v8_sample"]["status"]] += 1
            self.assertEqual(dict(verdicts), self.placement["stage_verdict_counts"][stage["stage"]])
            total[stage["stage"]] = sum(verdicts.values())
        self.assertEqual(dict(total), {"calibration": 54, "external": 69})
        self.assertEqual((self.cal["http_responses"], self.cloud["calibration_http_responses"],
                          self.cloud["prospective_http_responses"]), (54, 54, 69))
        self.assertEqual(self.cloud["http_responses"], sum(total.values()))
        self.assertEqual(self.placement["revision"], self.cleanup["revision"])
        self.assertEqual(self.placement["revision"], self.cloud["revision"])
        self.assertEqual(self.placement["http_status_counts"], {"200": sum(total.values())})
        self.assertEqual(self.placement["requests_per_recorded_instance"], [sum(total.values())])
        self.assertEqual((self.placement["distinct_recorded_instances"], self.placement["unknown_requests"]), (1, 0))
        self.assertTrue(self.placement["all_execution_logs_recorded"])
        self.assertEqual([self.cleanup[k] for k in ("service_http", "revision_http", "artifact_registry_package_http")],
                         [404, 404, 404])

    def test_actual_boundaries_include_the_mixed_distinct_window_verdict(self):
        result = score((DATA / "predictions.json").read_bytes(), self.cloud,
                       model_fields={"control": CANDIDATE, "expanded": CANDIDATE}, external=EXTERNAL,
                       candidate="expanded")
        self.assertEqual([result["summary"][m]["accurate_programs"] for m in ("control", "expanded")], [2, 1])
        self.assertFalse(any(s["passes_all_gates"] for s in result["summary"].values()))
        rows = [b for case in result["cases"].values() for b in case["boundaries"]]
        self.assertEqual(len(rows), 12)
        expected = {"control": (5, 1, 0), "expanded": (4, 1, 1), "historical_live": (5, 1, 0)}
        for model, counts in expected.items():
            native = Counter(b["comparisons"][model]["comparison"] for b in rows
                             if any(label.startswith("native_") for label in b["labels"]))
            self.assertEqual(tuple(native[k] for k in ("agreement", "mixed", "opposition")), counts)
        prospective = Counter(b["comparisons"]["expanded"]["comparison"] for b in rows
                              if any(label.startswith("candidate_") for label in b["labels"]))
        self.assertEqual(dict(prospective), {"agreement": 4, "opposition": 2})
        mixed = [b for b in result["cases"]["distinct-window"]["boundaries"] if len(set(b["native"])) > 1]
        self.assertEqual([(b["limit_ms"], b["native"].count(AC), b["native"].count(TLE)) for b in mixed],
                         [(30, 2, 1)])


if __name__ == "__main__":
    unittest.main()

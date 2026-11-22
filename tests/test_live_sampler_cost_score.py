import json
from pathlib import Path
import runpy
import unittest

STUDY = Path(__file__).resolve().parents[1] / "experiments/live_sampler_cost"
score = runpy.run_path(str(STUDY / "score.py"))
DESIGN = json.loads((STUDY / "design.json").read_bytes())


class LiveSamplerCost(unittest.TestCase):
    def test_gate_cadence_including_single_unfinished_visit(self):
        report = dict(schema=1, arm="gate", pid=19, threads=1, period=1024, loads=1025, pairs=1,
                      sites=[{"offset": 2, "loads": 1025, "pairs": 1}])
        self.assertEqual(score["read_sidecar"](json.dumps(report).encode(), "gate", 19, {}), {2: report["sites"][0]})
        for field, value in (("pairs", 2), ("arm", "empty"), ("threads", True), ("pid", 18)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                score["read_sidecar"](json.dumps({**report, field: value}).encode(), "gate", 19, {})

    def test_signature_and_hot_span_independent_of_address_differences_elsewhere(self):
        oracle = {"offset": 5, "visits": 1024, "pairs": 1, "near_pairs_by_base_mod64_div4": [0] * 16,
                  "sampled_span_bytes": 4096}
        rows = {}
        for rep in score["REPS"]:
            for arm in ("full", "gate", "fused"):
                sites = {i: {"loads": 1024, "pairs": 1} for i in (5, 9)}
                if arm != "gate":
                    for i in sites:
                        near = int(i == 9 and arm == "full")
                        sites[i].update(near_pairs=near, far_pairs=1-near, sampled_address_span_bytes=4096+near)
                rows["one", rep, arm] = {"sites": sites}
        cases = {"one": {"oracle": oracle}}
        result = score["compare_sites"](cases, rows)
        self.assertTrue(result["pass"])
        self.assertEqual(result["cases"]["one"]["descriptive_span_differences"], 6)
        rows["one", 1, "fused"]["sites"][5]["sampled_address_span_bytes"] = 0
        del rows["one", 2, "gate"]["sites"][9]
        failed = score["compare_sites"](cases, rows)
        self.assertFalse(failed["pass"])
        self.assertEqual(len(failed["failures"]), 2)

    def test_weighting_signed_differences_and_unrounded_thresholds(self):
        cases = {"walk": {"oracle": {}}, "cost_control": {}}
        rows = {(n, r, a): {"cpu_ns": (100 if n == "walk" else 1)*1_000_000_000}
                for n in cases for r in score["REPS"] for a in score["LIVE_ARMS"]}
        for r in score["REPS"]:
            rows["walk", r, "fused"]["cpu_ns"] = 94_000_000_000
            rows["cost_control", r, "fused"]["cpu_ns"] = 1_020_000_001
        result = score["costs"](cases, rows, DESIGN["estimation"]["comparisons"])
        live, full = result["fused_vs_live"], result["fused_vs_full"]
        self.assertTrue(live["overhead_gate"]["pass"])
        self.assertTrue(full["optimization_gate"]["pooled_pass"])
        self.assertFalse(full["optimization_gate"]["pass"])
        self.assertEqual(full["by_case"]["cost_control"]["pooled_ratio_ppm"], 1_020_000)
        all_cases = full["pooled"]["all"]
        self.assertEqual(all_cases["pooled_delta_ns"], 6*(-6_000_000_000 + 20_000_001))
        self.assertEqual(all_cases["paired_delta_ns"], {"min": -6_000_000_000, "median": -2_989_999_999.5,
                                                      "max": 20_000_001})


if __name__ == "__main__":
    unittest.main()

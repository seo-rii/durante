import json
from pathlib import Path
import runpy
import unittest

STUDY = Path(__file__).resolve().parents[1] / "experiments/cpp20_locality"
score = runpy.run_path(str(STUDY / "score.py"))
fixtures = runpy.run_path(str(STUDY / "fixtures.py"))
CASES = {c["case_id"]: c for c in json.loads((STUDY / "cases.json").read_bytes())}
PLAN = json.loads((STUDY / "analysis-plan.json").read_bytes())


class Cpp20Locality(unittest.TestCase):
    def test_oracle_matches_stepwise_walk_at_sampled_visits(self):
        self.assertEqual(list(CASES.values()), fixtures["manifest"]())
        for case in CASES.values():
            if "oracle" not in case:
                continue
            entries, steps = map(int, case["stdin"].split())
            index = 713
            samples = []
            for t in range(steps):
                if t % 1024 < 2:
                    samples.append(index * 4)
                index = (1664525 * index + 1013904223) % entries
            self.assertEqual(case["stdout"], f"{index}\n")
            oracle = case["oracle"]
            self.assertEqual(max(samples) - min(samples), oracle["sampled_span_bytes"])
            self.assertEqual(len(samples) // 2, oracle["pairs"])
            for phase in range(0, 64, 4):
                near = sum(abs((a + phase) // 64 - (b + phase) // 64) <= 1
                           for a, b in zip(samples[::2], samples[1::2]))
                self.assertEqual(near, oracle["near_pairs_by_base_mod64_div4"][phase // 4])

    def test_partial_pair_and_strict_sample_shape(self):
        sample = dict(schema=2, pid=17, period=1024, line_bytes=64, threads=1,
                      loads=1, pairs=0, near_pairs=0, far_pairs=0)
        site = {"offset": 5, "loads": 1, "pairs": 0, "near_pairs": 0, "far_pairs": 0,
                "sampled_address_span_bytes": 0}
        sample["sites"] = [site]
        case = {"oracle": {"offset": 5}}
        self.assertEqual(score["hot_site"](sample, "diag_span", 17, case), site)
        for field, bad in (("sampled_address_span_bytes", True), ("sampled_address_span_bytes", 1),
                           ("pairs", 1), ("unexpected", 0)):
            with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                score["hot_site"]({**sample, "sites": [{**site, field: bad}]}, "diag_span", 17, case)
        with self.assertRaises(ValueError):
            score["hot_site"](sample, "diag_near", 17, case)

    def test_only_two_anchors_fit_both_models_and_no_extrapolation(self):
        anchors, fresh = [PLAN["timing_test"][key] for key in ("calibration", "fresh")]
        names = [anchors[0], *fresh, anchors[1]]
        spans = {name: 2**i for i, name in enumerate(names)}
        rows = {}
        for i, name in enumerate(names):
            for rep in score["REPS"]:
                rows[name, rep, "native"] = {"cpu_ns": (1 + i) * 1_000_000}
                rows[name, rep, "diag"] = {"cycles_x16": (1 + i) * 16}
        result = score["timing"](names, rows, spans, PLAN)
        self.assertEqual(result["cases_within_20_percent"], {"span": 3, "latency_only": 3})
        # Changing held-out labels must never affect the fitted parameters or estimates.
        for rep in score["REPS"]:
            rows[fresh[0], rep, "native"]["cpu_ns"] = 8_000_000
        changed = score["timing"](names, rows, spans, PLAN)
        self.assertEqual(changed["latency_only_ns_per_cycle"], result["latency_only_ns_per_cycle"])
        self.assertEqual(changed["cases_within_20_percent"], {"span": 2, "latency_only": 2})
        for model in ("span", "latency_only"):
            self.assertEqual(changed["fresh"][fresh[0]][model]["estimates_ns"],
                             result["fresh"][fresh[0]][model]["estimates_ns"])
        spans[fresh[0]] = 32
        self.assertFalse(score["timing"](names, rows, spans, PLAN)["fresh"][fresh[0]]["span"]["pass"])

    def test_cost_uses_unrounded_cpu_sums_and_keeps_modes_separate(self):
        rows = {(n, rep, a): {"cpu_ns": 1_000_000_000} for n in CASES for rep in score["REPS"] for a in score["ARMS"]}
        for n in CASES:
            for rep in score["REPS"]:
                rows[n, rep, "live_span"]["cpu_ns"] = 1_050_000_000
                if n == "sort/1":
                    rows[n, rep, "diag_span"]["cpu_ns"] = 1_100_000_001
        result = score["costs"](CASES, rows)
        diag, live = (result[k] for k in ("diag_span_vs_diag", "live_span_vs_live"))
        self.assertTrue(diag["pooled_pass"] and live["passed"])
        self.assertFalse(diag["passed"] or diag["case_pass"]["sort/1"])
        self.assertEqual(diag["by_case"]["sort/1"]["pooled_ratio_ppm"], 1_100_000)
        self.assertEqual(diag["pooled"]["controls"]["pairs"], 12)


if __name__ == "__main__":
    unittest.main()

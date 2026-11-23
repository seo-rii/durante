import json
from pathlib import Path
import runpy
import unittest

HERE = Path(__file__).resolve().parents[1] / "experiments/live_sampler_fast"
score = runpy.run_path(str(HERE / "score.py"))
profiles = runpy.run_path(str(HERE / "profiles.py"))


class FastSampler(unittest.TestCase):
    def test_profile_prefix_order_ties_threshold_and_cap(self):
        result = profiles["select"]({7: 5, 8: 90, 6: 5})
        self.assertEqual(result["offsets"], [6, 8])
        self.assertEqual(result["selected_visits"], 95)
        capped = profiles["select"]({i: 1 for i in range(100, 0, -1)})
        self.assertEqual(capped["offsets"], list(range(1, 65)))

    def test_manifests_regenerate_from_only_archived_calibration_data(self):
        self.assertEqual(profiles["fixtures"](), json.loads((HERE / "cases.json").read_bytes()))
        result = profiles["profiles"]()
        self.assertEqual(result, json.loads((HERE / "profiles.json").read_bytes()))
        new = set(json.loads((HERE / "design.json").read_bytes())["cases"]["holdout"])
        self.assertTrue(all(not new.intersection(p["training_cases"]) for p in result["by_elf"].values()))

    def test_profiled_is_the_projection_and_all_repetitions_count_for_coverage(self):
        row = lambda off, n: {"offset": off, "loads": n, "pairs": 1, "near_pairs": 0, "far_pairs": 1,
                              "sampled_address_span_bytes": 9}
        cases = {"known": {"binary_sha256": "one", "split": "repeat"}, "new": {"binary_sha256": "one", "split": "holdout"}}
        data = {}
        for c in cases:
            for r in score["REPS"]:
                sites = {4: row(4, 90), 5: row(5, 10)}
                if c == "new" and r == 3:
                    sites[4]["loads"] = 89
                for a in ("catalog", "gate", "full", "packed", "profiled"):
                    observed = set(sites) | {6} if a == "catalog" else {i: dict(v) for i, v in sites.items()
                                                                       if a != "profiled" or i == 4}
                    data[c, r, a] = {"sites": observed}
        p = {"by_elf": {"one": {"offsets": [4]}}}
        integrity, coverage = score["check_sites"](cases, data, p, 7600)
        self.assertTrue(integrity["pass"])
        self.assertTrue(coverage["cases"]["known"]["pass"])
        self.assertFalse(coverage["cases"]["new"]["pass"])
        data["known", 1, "profiled"]["sites"][5] = row(5, 10)
        integrity, _ = score["check_sites"](cases, data, p, 7600)
        self.assertFalse(integrity["pass"])
        self.assertEqual(len(integrity["failures"]), 1)

    def test_population_specific_gates_do_not_hide_holdout_regression(self):
        cases = {"same": {"split": "repeat"}, "new": {"split": "holdout"}}
        rows = {(n, r, a): {"cpu_ns": 1000 if n == "new" else 1_000_000}
                for n in cases for r in score["REPS"] for a in ("live", "full", "packed", "profiled")}
        for r in score["REPS"]:
            rows["same", r, "profiled"]["cpu_ns"] = 800_000
            rows["new", r, "profiled"]["cpu_ns"] = 1021
        result = score["cost"](cases, rows, [["full", "profiled"], ["live", "profiled"]])
        gates = result["profiled_vs_full"]["gates"]
        self.assertTrue(gates["repeat"]["pass"] and gates["all"]["pooled_pass"])
        self.assertFalse(gates["holdout"]["pass"] or gates["all"]["pass"])
        self.assertTrue(result["profiled_vs_live"]["gates"]["all"]["pass"])


if __name__ == "__main__":
    unittest.main()

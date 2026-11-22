from fractions import Fraction
import json
from pathlib import Path
import runpy
import unittest

STUDY = Path(__file__).resolve().parents[1] / "experiments/first_run_sampler"
score = runpy.run_path(str(STUDY / "score.py"))


class FirstRunScore(unittest.TestCase):
    def test_interval_does_not_bill_hash_and_build_twice(self):
        base = dict(parent_cpu_ns=400, hash_cpu_ns=200, workflow_cpu_ns=2400, workflow_wall_ns=3200,
                    cpu_us=2, wall_us=3, user_us=1, sys_us=1, build_cpu_ns=100, lookup_cpu_ns=0,
                    arm="calibration")
        v = score["intervals"](base)
        self.assertEqual((v["workflow_cpu"], v["child_cpu"], v["ordinary_baseline_cpu"]), (2400, 2000, 2400))
        normal = {**base, "arm": "full", "build_cpu_ns": 0}
        self.assertEqual(score["intervals"](normal)["ordinary_baseline_cpu"], 2200)
        with self.assertRaises(ValueError):
            score["intervals"]({**base, "parent_cpu_ns": 100, "workflow_cpu_ns": 2100})

    def test_exact_ceil_and_nonpositive_savings(self):
        self.assertEqual(score["break_even"](15, Fraction(5, 2)), 6)
        self.assertEqual(score["break_even"](16, Fraction(5, 2)), 7)
        self.assertIsNone(score["break_even"](0, Fraction(0)))
        self.assertIsNone(score["break_even"](15, Fraction(-2)))

    def test_online_and_extra_run_amortization_are_different(self):
        held = {"x/2": {"program": "x", "population": "real"}, "x/4": {"program": "x", "population": "real"}}
        calibration = {"x": ("x/1", {"workflow_cpu": 1001, "build_cpu_ns": 30, "hash_cpu_ns": 31})}
        fake = {}
        for r in score["REPS"]:
            for n in held:
                full = 110 if n == "x/2" else 130
                fake[n,r,"full"] = {"workflow_cpu": full, "ordinary_baseline_cpu": full-20}
                fake[n,r,"profiled"] = {"workflow_cpu": 100, "ordinary_baseline_cpu": 100}
        integrity = {"cases": {n: {"coverage": {"pass": True}} for n in held}}
        result = score["amortization"](fake, calibration, held, integrity)["x"]
        self.assertEqual(result["workflow_cpu"]["mean_savings_ns"], "20")
        self.assertEqual(result["workflow_cpu"]["online_subsequent_inputs"], 2)
        self.assertEqual(result["workflow_cpu"]["online_total_required_inputs"], 3)
        self.assertEqual(result["workflow_cpu"]["standalone_required_inputs"], 51)
        self.assertIsNone(result["ordinary_baseline_cpu"]["online_total_required_inputs"])

    def test_exact_projection_with_zero_coverage_on_changed_path(self):
        cases = json.loads((STUDY / "cases.json").read_bytes())
        c = next(c for c in cases if c["case_id"] == "path_switch/switch")
        o = c["oracle"]
        site = dict(loads=o["visits"], pairs=o["pairs"], near_pairs=o["near_pairs"],
                    far_pairs=o["far_pairs"], sampled_address_span_bytes=o["sampled_address_span_bytes"])
        self.assertTrue(score["control_oracle"]({o["offset"]: site}, o))
        self.assertTrue(score["control_oracle"]({}, o, {o["inactive_offset"]}))
        self.assertFalse(score["control_oracle"]({}, o, {o["offset"]}))
        self.assertFalse(score["fast"]["coverage"]([(0, o["visits"])])["pass"])
        self.assertTrue(score["fast"]["coverage"]([(90,100)])["pass"])


if __name__ == "__main__":
    unittest.main()

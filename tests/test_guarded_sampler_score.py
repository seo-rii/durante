import json
from pathlib import Path
import runpy
import unittest

STUDY = Path(__file__).resolve().parents[1] / "experiments/guarded_sampler"
score = runpy.run_path(str(STUDY / "score.py"))
cases = {c["case_id"]: c for c in json.loads((STUDY / "cases.json").read_bytes())}


class GuardedScore(unittest.TestCase):
    def test_inclusive_ratio_without_rounding(self):
        rule = score["classified"]
        self.assertEqual(rule(9, 1), "use_profiled")
        self.assertEqual(rule(8_999_999, 1_000_001), "run_full")
        self.assertEqual(rule(0, 0), "run_full")

    def test_positive_shift_with_both_previously_observed_pcs(self):
        prefix, case = [cases["path_mix/" + n] for n in ("train", "shift")]
        prior, now = [c["mix_oracle"] for c in (prefix, case)]
        self.assertEqual([s["offset"] for s in prior], [s["offset"] for s in now])
        self.assertGreater(min(s["loads"] for s in prior), 0)
        self.assertNotEqual(prefix["stdout"], case["stdout"])
        self.assertEqual(score["classified"](*[s["loads"] for s in prior]), "use_profiled")
        self.assertEqual(score["classified"](*[s["loads"] for s in now]), "run_full")
        selected = {now[0]["offset"]}
        sampled = {now[0]["offset"]: now[0]}
        self.assertTrue(score["control_matches"](case, sampled, selected))
        self.assertFalse(score["control_matches"](case, sampled))

    def test_inaccurate_omitted_counter_fails_even_if_same_decision(self):
        name = "path_mix/shift"
        case = cases[name]
        sites = {s["offset"]: s for s in case["mix_oracle"]}
        primary = case["mix_oracle"][0]["offset"]
        profile = {case["binary_sha256"]: [primary]}
        rows = {}
        for r in score["REPS"]:
            selected = {primary: sites[primary]}
            rows[name,r,"full"] = {"sites": sites}
            rows[name,r,"profiled"] = {"sites": selected}
            rows[name,r,"guarded"] = {"sites": selected, "decision": {
                "selected_visits": 400000, "omitted_visits": 7600000, "total_visits": 8000000, "status": "run_full"}}
        self.assertTrue(score["integrity"]({name: case}, rows, profile)["pass"])
        rows[name,8,"guarded"]["decision"].update(omitted_visits=7600001, total_visits=8000001)
        self.assertFalse(score["integrity"]({name: case}, rows, profile)["pass"])

    def test_projected_cost_includes_full_only_on_failed_jobs(self):
        names = {"keep": {"population": "real"}, "reject": {"population": "control"}}
        obs = {}
        for n in names:
            for r in score["REPS"]:
                obs[n,r,"full"] = {"workflow_cpu": 30}
                obs[n,r,"guarded"] = {"workflow_cpu": 20, "decision": {"status": "run_full" if n == "reject" else "use_profiled"}}
        out = score["projected"](names, obs)
        self.assertEqual(out["by_case"]["keep"]["candidate_cpu_ns"], 8*20)
        self.assertEqual(out["by_case"]["reject"]["candidate_cpu_ns"], 8*(20+30))


if __name__ == "__main__":
    unittest.main()

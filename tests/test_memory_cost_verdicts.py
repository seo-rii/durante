"""Check rounding, threshold equality, repeated observations and the blind join."""
import copy
from fractions import Fraction
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from memory_cost_model import CANDIDATE
from score_memory_cost import AC, TLE, agreement, limits, score, score_case


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        identity = {k: "a" * 64 for k in ("elf_sha256", "input_sha256", "output_sha256")}
        self.frozen = {**identity, "predictions_ns": {"baseline": [80_000_000] * 3, "candidate": [100_000_000] * 3},
                       "baseline_median_ns": 80_000_000, "candidate_median_ns": 100_000_000,
                       "counter_spread": dict.fromkeys(CANDIDATE, 0)}
        self.cloud = {**identity, "rounds": [{"repetition": i, "native_before_ns": 100_000_000,
                                              "native_after_ns": 100_000_000} for i in (1, 2, 3)],
                      "boundaries": [{"limit_ms": 80, "native": [TLE] * 3, "v8": AC},
                                     {"limit_ms": 120, "native": [AC, TLE, AC], "v8": AC}]}

        for run in self.cloud["rounds"]:
            for phase in ("before", "after"):
                run["native_" + phase] = {"status": AC, "cpu_ns": run[f"native_{phase}_ns"]}
        for row, side, verdict in zip(self.cloud["boundaries"], ("low", "high"), (TLE, AC)):
            row.update(native_samples=[{"status": v} for v in row["native"]], v8_sample={"status": row["v8"]},
                       roles=["native_" + side, "candidate_" + side], candidate=verdict)

    def test_half_up_and_minimum_at_rounded_boundaries(self):
        self.assertEqual(limits(625_000), {"low": 1, "high": 1})
        self.assertEqual(limits(Fraction(3_750_000, 2)), {"low": 2, "high": 2})

    def test_shared_limits_and_equality(self):
        result = score_case("fixture", self.frozen, self.cloud)
        self.assertTrue(result["models"]["baseline"]["within_20_percent"])
        low, high = result["boundaries"]
        self.assertEqual(low["labels"], ["native_low", "candidate_low"])
        self.assertEqual(low["comparisons"]["baseline"]["verdict"], AC)
        self.assertEqual(low["comparisons"]["candidate"]["comparison"], "agreement")
        self.assertEqual(low["comparisons"]["historical_live"]["comparison"], "opposition")
        self.assertEqual(high["comparisons"]["candidate"]["native_matches"], 2)
        self.assertEqual(high["comparisons"]["candidate"]["comparison"], "mixed")

    def test_case_identity_repeats_and_unique_limits_are_checked(self):
        for field, bad in (("output_sha256", "b" * 64), ("rounds", self.cloud["rounds"][:2]),
                           ("boundaries", self.cloud["boundaries"] * 2)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                score_case("fixture", self.frozen, {**self.cloud, field: bad})

    def test_all_repetitions_must_pass_accuracy(self):
        changed = copy.deepcopy(self.frozen)
        changed["predictions_ns"]["candidate"][0] = 79_999_999
        result = score_case("fixture", changed, self.cloud)
        self.assertFalse(result["models"]["candidate"]["within_20_percent"])
        self.assertEqual(agreement(TLE, [TLE, AC, AC]), "mixed")

    def test_summaries_must_match_original_observations(self):
        for key, bad in (("native_samples", [{"status": AC}] * 3), ("v8_sample", {"status": TLE})):
            changed = copy.deepcopy(self.cloud)
            changed["boundaries"][0][key] = bad
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "contradicts"):
                score_case("fixture", self.frozen, changed)

    def test_published_all_program_gates_and_native_verdicts(self):
        data = Path(__file__).resolve().parents[1] / "experiments/results/cpp20-v8-memory"
        result = score((data / "predictions.json").read_bytes(), json.loads((data / "cloud.json").read_bytes()))
        for model in ("baseline", "candidate"):
            self.assertEqual(result["summary"][model]["accurate_programs"], 2)
            self.assertFalse(result["summary"][model]["passes_all_gates"])
        self.assertFalse(result["cases"]["fenwick-prefix"]["native_spread_pass"])
        bounds = [row for case in result["cases"].values() for row in case["boundaries"]]
        self.assertEqual(len(bounds), 12)
        self.assertEqual(sum(r["comparisons"]["candidate"]["native_matches"] for r in bounds), 30)

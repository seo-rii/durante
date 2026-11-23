"""Check the one-seed-per-executable projection on the verified contest archive."""
from fractions import Fraction
import json
from math import ceil
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from contest_accuracy import seed_replay
from shape_calibration.verify_combined import unpack


class SeedProjection(unittest.TestCase):
    def test_strict_threshold_and_mixed_majority(self):
        deadlines = [dict(limit_ms=1, fixed=True, adaptive_percent=[85]),
                     dict(limit_ms=2, fixed=False, adaptive_percent=[115])]
        score = seed_replay.project(1_000_000, [1_000_000] * 3 + [1_999_999] * 6, deadlines)
        first, second = score["limits"]
        self.assertEqual((first["classification"], first["majority_match"], first["model_tle_reps"]),
                         ("mixed_native", False, 0))
        self.assertEqual((first["fixed"], first["adaptive"]), (True, True))
        self.assertEqual((second["classification"], second["majority_match"]), ("agreement", True))

    def test_recomputes_each_seed_ratio_and_matches_archive(self):
        results = ROOT / "experiments/results/gce-contest-deadlines"
        with tempfile.TemporaryDirectory() as temp:
            raw = unpack(results / "output.tar.xz", Path(temp))
            result = seed_replay.analyze(raw)
            self.assertEqual(result, json.loads((results / "seed.json").read_bytes()))
            for name, case in result["cases"].items():
                with self.subTest(case=name):
                    seed = json.loads((raw / "cases" / case["seed"] / "policy.json").read_bytes())
                    production = json.loads((raw / "cases" / name / "full/full/production.json").read_bytes())
                    weights = {key: Fraction(value) for key, value in seed["combined_reference_weights"].items()}
                    def work(counters):
                        return sum(weight * counters[key] for key, weight in weights.items())
                    predicted = ceil(seed["native"]["cpu_ns"] * work(production) / work(seed["anchor"]))
                    self.assertEqual(case["seed_projection"]["prediction_ns"], predicted)
            self.assertEqual(result["observed_refusal_reasons"]["shape/insufficient_material_coverage"], 24)
            self.assertEqual(result["projection_totals"]["all"], dict(
                limits=64, majority_matches=62, agreement=59, false_tle=0, false_accept=1, mixed_native=4))
            problem = result["cases"]["plane/tight_transfers"]
            seed_at_5 = next(p for p in problem["seed_projection"]["limits"] if p["limit_ms"] == 5)
            actual_at_5 = next(p for p in problem["observed"]["deadlines"] if p["limit_ms"] == 5)
            self.assertEqual(seed_at_5["classification"], "false_accept")
            self.assertTrue(actual_at_5["baseline_tle"] and actual_at_5["exact_tle"])


if __name__ == "__main__":
    unittest.main()

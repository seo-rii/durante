"""Verify the published ambiguity and infeasibility witnesses using only calibration timings."""
from fractions import Fraction
from importlib.util import find_spec
import json
from math import ceil, floor
from pathlib import Path
import sys
import unittest

EXP = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXP))
from crossed_memory_bounds import study
from crossed_memory_model import (CANDIDATE, LABELS, LABELS_SHA256, ORIGINAL, ORIGINAL_FEATURES_SHA256,
                                  new_cases, read_pinned, samples, training_rows)
from memory_cost_capacity import normalized_rows, verify


class PublishedBoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = EXP / "results/cpp20-crossed-memory"
        cls.report = json.loads((folder / "bounds.json").read_bytes())
        cls.seal = json.loads((folder / "predictions.json").read_bytes())
        historic = read_pinned(ORIGINAL / "features.json", ORIGINAL_FEATURES_SHA256)
        labels = read_pinned(LABELS, LABELS_SHA256)
        features = read_pinned(folder / "features.json", cls.report["features_sha256"])
        calibration = read_pinned(folder / "calibration.json", cls.report["calibration_sha256"])
        manifest = read_pinned(EXP / "crossed_memory_corpus/manifest.json", cls.report["manifest_sha256"])
        original = training_rows(historic, labels)
        additional, external = new_cases(features, calibration, manifest, historic)
        cls.cohorts = {"original": original, "expanded": original + additional}
        cls.targets = {name: rows for name, (_, rows) in external.items()}
        cls.targets["fenwick-prefix"] = samples(historic["external"]["fenwick-prefix"])

    def test_certified_intervals_and_feasibility(self):
        self.assertEqual(self.report["fields"], list(CANDIDATE))
        for key in ("features_sha256", "calibration_sha256", "manifest_sha256"):
            self.assertEqual(self.report[key], self.seal[key])
        self.assertEqual(self.report["original_features_sha256"], ORIGINAL_FEATURES_SHA256)
        self.assertEqual(self.report["original_labels_sha256"], LABELS_SHA256)
        for name, cases in self.cohorts.items():
            arm = self.report[name]
            self.assertEqual(arm["training_cases"], len(cases))
            rows = [([row[k] for k in CANDIDATE], Fraction(c["native"]), Fraction(c["native"]))
                    for c in cases for row in c["rows"]]
            self.assertEqual(arm["training_replays"], len(rows))
            minimax = arm["minimax"]
            low, high = verify(*normalized_rows(rows), minimax["certificate"])
            self.assertEqual(minimax["lower_error_ppb"], floor(low * 10**9))
            self.assertEqual(minimax["upper_error_ppb"], ceil(high * 10**9))
            self.assertLessEqual(high - low, Fraction(1, 10**9))
            self.assertEqual(set(arm["tolerances"]), {"6", "20"})
            for percent, result in arm["tolerances"].items():
                tolerance = Fraction(int(percent), 100)
                if name == "expanded":
                    self.assertGreater(low, tolerance)
                    self.assertEqual(result, {"feasible": False})
                    continue
                self.assertLessEqual(high, tolerance)
                self.assertIs(result["feasible"], True)
                self.assertEqual(set(result["cases"]), set(self.targets))
                for case, extremes in result["cases"].items():
                    targets = [tuple(row[k] for k in CANDIDATE) for row in self.targets[case]]
                    self.assertEqual(extremes["distinct_work_vectors"], len(set(targets)))
                    self.assertLessEqual(extremes["min"]["prediction_ns"], extremes["max"]["prediction_ns"])
                    for side in ("min", "max"):
                        with self.subTest(percent=percent, case=case, side=side):
                            witness = extremes[side]
                            weights = [Fraction(str(v)) for v in witness["ns_per_unit"]]
                            self.assertEqual(len(weights), len(CANDIDATE))
                            self.assertGreaterEqual(min(weights), 0)

                            def predict(counts):
                                return sum(w * x for w, x in zip(weights, counts))

                            worst = max(abs(predict(x) / center - 1) for x, center, _ in rows)
                            self.assertLessEqual(worst, tolerance + Fraction(1, 10**12))
                            self.assertAlmostEqual(float(worst), witness["worst_training_relative_error"], places=12)
                            self.assertIn(witness["repetition"], (1, 2, 3))
                            expected = predict(targets[witness["repetition"] - 1])
                            self.assertAlmostEqual(float(expected), witness["prediction_ns"], delta=1e-5)

    @unittest.skipUnless(find_spec("numpy") and find_spec("scipy"), "LP replay needs NumPy and SciPy")
    def test_solver_reproduces_reported_extrema(self):
        for name, cases in self.cohorts.items():
            rebuilt = study(cases, self.targets)["tolerances"]
            for percent, recorded in self.report[name]["tolerances"].items():
                self.assertEqual(rebuilt[percent]["feasible"], recorded["feasible"])
                for case, extrema in recorded.get("cases", {}).items():
                    for side in ("min", "max"):
                        actual = rebuilt[percent]["cases"][case][side]["prediction_ns"]
                        self.assertAlmostEqual(actual, extrema[side]["prediction_ns"], delta=0.1)


if __name__ == "__main__":
    unittest.main()

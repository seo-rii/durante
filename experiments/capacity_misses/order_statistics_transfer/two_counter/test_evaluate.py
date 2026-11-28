import json
import unittest
from fractions import Fraction

import evaluate


class WitnessValidationTests(unittest.TestCase):
    def test_inclusive_equality_and_indeterminate_pairs(self):
        self.assertEqual(evaluate.decision([8, 10], [10, 12], 1), "accept")
        self.assertEqual(evaluate.decision([12, 13], [10, 12], 1), "indeterminate")
        self.assertEqual(evaluate.decision([13, 14], [10, 12], 1), "timeout")
        self.assertEqual(evaluate.outcome("indeterminate", "timeout"), "indeterminate")

    def test_signed_median_error_and_exact_coverage(self):
        sample = {"r":{"native":[3],"two_counter":[5],"ML4":[1]},
                  "s":{"native":[3],"two_counter":[6],"ML4":[Fraction(7999999, 10000000)]}}
        out = evaluate.compare(["i"], {"i":["r","s"]}, [1], sample)
        scores = out["cases"]["s"]["models"]
        self.assertTrue(scores["two_counter"]["within_20_percent"])
        self.assertFalse(scores["ML4"]["within_20_percent"])
        self.assertEqual(out["summary"]["two_counter"]["false_timeout"], 1)

    def test_every_original_holdout_and_development_row(self):
        result = evaluate.replay()
        self.assertEqual(result, json.loads((evaluate.HERE / "results.json").read_bytes()))
        original = json.loads((evaluate.STUDY / "results.json").read_bytes())
        for phase, panels in result.items():
            for label, parent in (("predecessor_development","predecessor"),("inversion_holdout","inversions")):
                rows = panels[label]["cases"]
                self.assertEqual(len(rows), 4)
                for case, data in rows.items():
                    old = original[phase][parent]
                    self.assertEqual(len(data["deadlines"]), 5)
                    self.assertEqual(data["native_bounds"], old["timing"][case]["native_bounds"])
                    self.assertEqual(data["models"]["ML4"]["score_ratio"], old["timing"][case]["models"]["ML4"]["median_ratio"])
                    for f, d in data["deadlines"].items():
                        self.assertEqual((d["native"],d["ML4"]),
                                         (old["decisions"][case][f]["native"],old["decisions"][case][f]["ML4"]))


if __name__ == "__main__":
    unittest.main()

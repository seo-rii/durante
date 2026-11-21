from fractions import Fraction
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "experiments/access_sample"
RESULT = ROOT / "experiments/results/gce-access-sample"
with patch.object(sys, "path", [str(STUDY), *sys.path]):
    evaluation = runpy.run_path(str(STUDY / "score.py"))


class SampleClock(unittest.TestCase):
    def test_extrapolation_uses_each_sites_own_pair_fraction(self):
        sample = {"sites": [{"loads": 1026, "pairs": 2, "far_pairs": 1},
                            {"loads": 2, "pairs": 1, "far_pairs": 1},
                            {"loads": 1, "pairs": 0, "far_pairs": 0}],
                  "loads": 1029, "pairs": 3, "far_pairs": 2}
        phase = {"sample": sample, "production": {"latency_bound": {"cycles_x16": 16*100},
                                                  "memory_operand_bytes": {"bytes": 20}}}
        result = evaluation["load_features"](phase, {"latency_cycles": Fraction(1, 2), "memory_bytes": 1})
        self.assertEqual(result["baseline"], 70)
        self.assertEqual(result["far"], 515)
        self.assertEqual(result["far_share"], Fraction(515, 1028))
        self.assertEqual(result["unpaired_loads"], 1)
        self.assertEqual(result["scaled_far"], Fraction(515*100, 5*1029))

    def test_coefficient_uses_only_training_rows_and_cannot_discount_the_clock(self):
        def case(native, feature):
            return {"native": [native]*6, "features": [{"baseline": 20, "far": feature}]*3}
        cases = {"a": case(70, 50), "b": case(220, 200), "withheld": case(800, 1)}
        self.assertEqual(evaluation["fit_penalty"](cases, ["a", "b"], "far"), 1)
        self.assertEqual(evaluation["fit_penalty"]({"a": case(10, 40)}, ["a"], "far"), 0)

    def test_archived_controls_match_the_known_address_sequences(self):
        check = subprocess.run([sys.executable, STUDY / "check_locality.py", RESULT / "dataset.json.gz"],
                               capture_output=True, text=True, timeout=60)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_archived_scores_replay(self):
        check = subprocess.run([sys.executable, STUDY / "score.py", RESULT / "dataset.json.gz",
                                "--verify", RESULT / "evaluation.json"], capture_output=True, text=True, timeout=60)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)


if __name__ == "__main__":
    unittest.main()

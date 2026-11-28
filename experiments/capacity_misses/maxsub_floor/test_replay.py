from fractions import Fraction as F
import unittest
import replay


class FloorTests(unittest.TestCase):
    def test_floor_above_below_and_at_reference(self):
        self.assertEqual(replay.score_ratio(F(5), F(3), "floor", 7), F(9, 7))
        for ref in (7, 8):
            self.assertEqual(replay.score_ratio(F(10), F(ref), "floor", 7), F(10, ref))
        self.assertEqual(replay.score_ratio(F(5), F(3), "global", 7), F(6, 5))

    def test_varying_reference_scores_are_corrected_before_division(self):
        obs = {"r": ([F(4)], [F(3), F(8)]), "s": ([F(5)], [F(5)])}
        proto = {"intercept_ns": 7, "groups": {"g": ["r", "s"]}, "factors": [1]}
        row = replay.evaluate("test", obs, proto, "floor")["cases"]["s"]
        self.assertEqual(row["model"], ["5/8", "9/7"])
        self.assertEqual(row["outcomes"], ["abstain"])

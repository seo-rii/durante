import unittest
from fractions import Fraction as F

from replay import crossing, fit_offset, floor_offset, score_case


class OffsetTests(unittest.TestCase):
    def test_offset_matches_exact_ratio(self):
        self.assertEqual(crossing(15, 5, F(2)), 5)
        self.assertIsNone(crossing(8, 5, F(1)))
        self.assertLess(crossing(8, 5, F(3, 4)), 0)

    def test_fit_excludes_other_problems_and_impossible_crossings(self):
        def example(name, family, solver, native):
            return dict(family=family, boot="primary", solver=name, score=solver,
                        ref_score=2_000_000, native_ratio=F(native))
        rows = [example("positive", "string_matching", 13_000_000, 2),
                example("negative", "string_matching", 1_000_000, 2),
                example("tie", "string_matching", 1_000_000, 1),
                example("other", "dijkstra", 100_000_000, 2)]
        offset, values = fit_offset(r for r in rows if r["family"] == "string_matching")
        self.assertEqual(offset, 9_000_000)
        self.assertEqual(set(values), {"string_matching/primary/positive", "string_matching/primary/negative",
                                       "string_matching/primary/tie"})

    def test_continuous_reference_floor_and_inclusive_deadline(self):
        self.assertEqual([floor_offset(s, 7) for s in (5, 7, 9)], [2, 0, 0])
        row = dict(score=F(17), ref_score=F(7), truth=(F(2), F(2)), native_ratio=F(2))
        result = score_case(row, 3)
        self.assertEqual(result["prediction"], "2")
        self.assertEqual(result["outcomes"], ["correct"] * 5)
        self.assertEqual(score_case(row, 0)["outcomes"][3], "false_timeout")

    def test_one_times_cannot_reverse_rank(self):
        for score in (4, 5, 6):
            row = dict(score=score, ref_score=5, truth=(F(1), F(1)), native_ratio=F(1))
            for offset in (1, 100):
                adjusted = F(score_case(row, offset)["prediction"])
                self.assertEqual(adjusted < 1, score < 5)
                self.assertEqual(adjusted == 1, score == 5)

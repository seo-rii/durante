from fractions import Fraction
import unittest

from replay import score, stable_counts, summary


class RecurrenceReplayTests(unittest.TestCase):
    def test_repetitions_cover_every_program_and_preserve_real_zero(self):
        self.assertEqual(stable_counts({"r": [0, 0]}, ["r"], 2), {"r": 0})
        for bad in ({}, {"r": [False, 0]}, {"r": [1]}, {"r": [1, 2]}, {"r": [-1, -1]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                stable_counts(bad, ["r"], 2)

    def test_charge_both_sides_and_count_lost_deadline_despite_better_median(self):
        row = dict(score=Fraction(2), ref_score=Fraction(1), native_ratio=Fraction(3, 2),
                   truth=(Fraction(7, 5), Fraction(3, 2)))
        old, new = score(row, 0, 0), score(row, 0, Fraction(3, 5))
        self.assertEqual(new["prediction"], "5/4")
        result = summary([dict(study="study", solver="alternate", baseline=old, recurrence=new)])
        self.assertEqual(result["new_false_decisions"], [["study", "alternate", "5/4", "correct", "false_accept"]])
        self.assertEqual(result["corrected_decisions"], [["study", "alternate", "3/2", "false_timeout", "correct"]])
        self.assertFalse(result["meets_criterion"])

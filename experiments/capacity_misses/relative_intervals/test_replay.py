import unittest
from fractions import Fraction as F

from replay import Interval, Observation, calibration, evaluate, record


def interval(a, b):
    return Interval(F(a), F(b))


class RelativeIntervalTests(unittest.TestCase):
    def test_uses_every_native_and_reference_extreme(self):
        self.assertEqual(Interval.from_runs([2, 3], [4, 6]), interval("1/3", "3/4"))
        for solver, reference in (([], [1]), ([0, 2], [1]), ([1], [-1]), ([1], [])):
            with self.assertRaises(ValueError):
                Interval.from_runs(solver, reference)

    def test_both_bounds_keep_the_runs_that_decide_them(self):
        rows = [Observation("f", "b", name, interval(*native), interval(*model))
                for name, native, model in [("one", [2, 6], [3, 4]), ("two", [3, 7], [2, 3])]]
        self.assertEqual(calibration(rows), interval("1/2", "7/2"))

    def test_inclusive_upper_and_strict_lower(self):
        observed = interval(2, 3)
        self.assertEqual(observed.verdict(F(2)), "mixed")
        self.assertEqual(observed.verdict(F(3)), "accept")
        self.assertEqual(observed.verdict(F("19/10")), "timeout")

    def test_mixed_native_issued_and_abstained_stay_out_of_accuracy(self):
        samples = {"ref": ([10], [10]), "var": ([10, 20], [10])}
        rows = list(record("family", "boot", ["ref", "var"], samples))
        result = evaluate(rows, interval(1, 1), [F(1), F(2)])
        self.assertEqual(result["issued_on_decisive"], [1, 1])
        self.assertEqual(result["counts"]["issued_native_mixed"], 1)
        self.assertEqual(result["issued_verdicts"], {"accept": 2, "timeout": 0})
        widened = evaluate(rows, interval(1, 2), [F(1), F(2)])
        self.assertEqual(widened["counts"]["abstain"], 1)
        self.assertEqual(widened["counts"]["issued_native_mixed"], 0)

    def test_counts_both_wrong_verdict_directions(self):
        cases = [Observation("f", "b", "a", interval(2, 3), interval(1, 1)),
                 Observation("f", "b", "b", interval(1, 1), interval(2, 3))]
        result = evaluate(cases, interval(1, 1), [F(1)])
        self.assertEqual(result["counts"]["false_timeout"], 1)
        self.assertEqual(result["counts"]["false_accept"], 1)

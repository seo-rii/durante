from fractions import Fraction
import unittest

import replay


class ScoreTests(unittest.TestCase):
    def test_offset_precedes_division_and_equality_accepts(self):
        observations = {"r": ([Fraction(1)], [Fraction(5)]), "s": ([Fraction(2)], [Fraction(11)])}
        proto = {"factors": [1, 1.5, 2, 3], "groups": {"g": ["r", "s"]}}
        old = replay.evaluate("test", observations, proto, 0)
        new = replay.evaluate("test", observations, proto, 1)
        self.assertEqual(old["counts"]["false_timeout"], 1)
        self.assertEqual(new["cases"]["s"]["median_prediction"], "2")
        self.assertTrue(replay.criterion(old, new))

    def test_each_error_direction_is_separate(self):
        def entry(error, accepts, timeouts):
            return {"median_absolute_relative_error": error, "counts": {"false_accept": accepts, "false_timeout": timeouts}}
        old = entry("1/2", 1, 2)
        self.assertFalse(replay.criterion(old, entry("1/4", 2, 0)))
        self.assertFalse(replay.criterion(old, entry("1/4", 0, 3)))
        self.assertFalse(replay.criterion(old, old))

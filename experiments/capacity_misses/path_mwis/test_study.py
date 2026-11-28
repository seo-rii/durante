from fractions import Fraction
import itertools
import unittest

import replay


class StudyTests(unittest.TestCase):
    def test_oracle_against_all_independent_subsets(self):
        for n in range(6):
            for weights in itertools.product((-2, 0, 3), repeat=n):
                expected = max(sum(w for i, w in enumerate(weights) if mask & (1 << i))
                               for mask in range(1 << n) if not mask & (mask << 1))
                self.assertEqual(replay.inputs["optimum"](weights), expected)

    def test_pair_recurrence_with_misses_from_same_run(self):
        branch = [{"branch": {"gshare_mispredicts": m}, "loop_recurrence_cycles_x16": c}
                  for m, c in [(1, 7), (4, 3)]]
        self.assertEqual(replay.recurrence_scores([2, 3], branch, 2, Fraction(1, 2)),
                         [Fraction(15, 2), Fraction(23, 2), Fraction(17, 2), Fraction(25, 2)])

    def test_regression_on_one_cell_cannot_be_offset_by_fixing_another(self):
        before = {"cases": {"table": {"outcomes": ["correct", "false_timeout", "issued_native_mixed"]}}}
        after = {"cases": {"table": {"outcomes": ["false_accept", "correct", "issued_native_mixed"]}}}
        self.assertEqual(replay.new_errors(before, after), [["table", 0]])

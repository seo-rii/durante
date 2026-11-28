from fractions import Fraction
import json
import unittest

import replay


class AnchoredClockTest(unittest.TestCase):
    def test_reference_uncertainty_includes_all_cross_pairs(self):
        cases = [Fraction(2), Fraction(5)]
        anchors = [Fraction(2), Fraction(3)]
        cpu = [4, 11]
        pairs = [s*n/a for s in cases for a in anchors for n in cpu]
        self.assertEqual(replay.anchored_range(cases, anchors, cpu), (min(pairs), max(pairs)))

    def test_absolute_equality_passes_and_crossing_is_indeterminate(self):
        self.assertEqual(replay.verdict((61, 65), 65), "accept")
        self.assertEqual(replay.verdict((65, 66), 65), "indeterminate")
        self.assertEqual(replay.verdict((66, 70), 65), "timeout")

    def test_cheap_clock_rounds_after_summing_integer_terms(self):
        clock = dict(instruction_ns_numerator=2, rep_byte_ns_numerator=2, ns_denominator=5)
        counter = dict(instructions=9, movs_bytes=1, stos_bytes=1)
        self.assertEqual(replay.cheap_nanoseconds(counter, clock), 5)

    def test_registered_clock_and_input(self):
        replay.validate_registration(replay.read(replay.HERE / "frozen.json"), replay.read(replay.HERE / "registration.json"))


    def test_published_deadlines_and_both_timeout_controls(self):
        result = replay.replay()
        self.assertEqual(json.loads(json.dumps(result)), replay.read(replay.HERE / "results.json"))
        for phase in ("primary", "repeat", "pooled"):
            panel = result[phase]
            self.assertEqual(panel["excluding_anchor"]["ML4"], {"agree": 6})
            self.assertEqual(panel["excluding_anchor"]["cheap"], {"agree": 4, "false_timeout": 2})
            for name in ("block4", "slow"):
                self.assertEqual(panel["cases"][name]["verdicts"]["ML4"], "timeout")


if __name__ == "__main__":
    unittest.main()

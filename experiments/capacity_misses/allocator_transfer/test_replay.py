from fractions import Fraction
import unittest

from replay import decision, equality_cost, parse_profile


RECEIPT = "ENTRIES 2 4 2 1 0\nBALANCE 3 4 0\nREQUESTED 80\nSIZE 32 1\nSIZE 48 1\n"


class AllocationTests(unittest.TestCase):
    def test_peak_is_distinct_from_total(self):
        result = parse_profile(RECEIPT)
        self.assertEqual((result["entries"]["_Znwm"], result["peak"]), (2, 4))
        self.assertEqual(result["sizes"], {32: 1, 48: 1})

    def test_incomplete_and_ambiguous_allocation_traffic(self):
        for receipt in ("", RECEIPT.replace("2 4 2 1 0", "2 4 2 1 1"),
                        RECEIPT.replace("3 4 0", "3 4 1"), RECEIPT + "SIZE 48 1\n",
                        RECEIPT.replace("REQUESTED 80", "REQUESTED 24")):
            with self.subTest(receipt=receipt), self.assertRaises(ValueError):
                parse_profile(receipt)

    def test_charge_must_include_reference_traffic(self):
        threshold = equality_cost(7, 5, 5, 1)
        self.assertEqual(threshold, 1)
        self.assertEqual(7 + threshold * 5, 2 * (5 + threshold))
        with self.assertRaises(ValueError):
            equality_cost(7, 5, 2, 1)

    def test_boundaries_and_mixed_runs(self):
        r = Fraction
        for native, predicted, expected in (((r(1), r(2)), r(2), "correct"),
                                             ((r(2), r(3)), r(1), "native_mixed"),
                                             ((r(3), r(4)), r(2), "false_accept"),
                                             ((r(1), r(1)), r(3), "false_timeout")):
            self.assertEqual(decision(native, predicted, r(2)), expected)

"""Check work-ratio envelopes without fitting weights or selecting a timing prediction."""
from fractions import Fraction
from itertools import product
from math import ceil
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from contest_submissions.bounds import bounds


def counts(i, rep, latency, memory):
    return dict(instructions=i, rep_bytes=rep, cycles_x16=latency, memory_operand_bytes=memory)


class SubmissionBounds(unittest.TestCase):
    def test_integer_rounding_precedes_conservative_error_floor(self):
        result = bounds(counts(4, 4, 3, 3), counts(1, 3, 4, 4), [103, 100, 101], [80] * 8 + [130])
        self.assertEqual(result["instruction_rep"], dict(lower_ns=25, upper_ns=78, minimum_error_ppm=25000,
                                                        direction="under"))
        self.assertEqual(result["latency_memory"], dict(lower_ns=134, upper_ns=138, minimum_error_ppm=30769,
                                                       direction="over"))

    def test_touching_interval_and_sub_ppm_gap(self):
        a = counts(1, 1, 1, 1)
        ref = [2_000_000] * 3
        for target, direction in ((2_000_001, "under"), (2_000_000, None), (1_999_999, "over")):
            with self.subTest(target=target):
                for value in bounds(a, a, ref, [target] * 9).values():
                    self.assertEqual((value["minimum_error_ppm"], value["direction"]), (0, direction))

    def test_absent_reference_feature_and_zero_numerator(self):
        source = counts(8, 0, 16, 0)
        target = counts(16, 6, 0, 0)
        result = bounds(source, target, [99, 100, 101], [250] * 9)
        self.assertEqual(result["instruction_rep"], dict(lower_ns=198, upper_ns=None, minimum_error_ppm=0,
                                                        direction=None))
        self.assertEqual(result["latency_memory"], dict(lower_ns=0, upper_ns=0, minimum_error_ppm=1_000_000,
                                                       direction="under"))
        small = bounds(source, target, [99, 100, 101], [150] * 9)["instruction_rep"]
        self.assertEqual((small["minimum_error_ppm"], small["direction"]), (320000, "over"))
        with self.assertRaisesRegex(ValueError, "no positive reference work"):
            bounds(counts(0, 0, 16, 0), target, [100] * 3, [150] * 9)

    def test_counter_units_and_sample_order_do_not_change_bounds(self):
        source, target = counts(10, 18, 120, 2), counts(3, 90, 9, 11)
        paid, native = [125, 142, 103], [210, 239, 145] * 3
        result = bounds(source, target, paid, native)
        factors = counts(16, 64, 2, 2048)
        for values in (source, target):
            for col, factor in factors.items():
                values[col] *= factor
        self.assertEqual(result, bounds(source, target, list(reversed(paid)), list(reversed(native))))

    def test_nonnegative_weights_attain_extrema_and_stay_inside(self):
        source, target = counts(2, 7, 2, 7), counts(1, 22, 1, 22)
        paid, native = [105, 113, 130], [175] * 9
        envelope = bounds(source, target, paid, native)["instruction_rep"]
        forecast = [ceil(t * Fraction(a + 22 * b, 2 * a + 7 * b))
                    for t in paid for a, b in product(range(4), repeat=2) if a + b > 0]
        self.assertEqual(min(forecast), envelope["lower_ns"])
        self.assertEqual(max(forecast), envelope["upper_ns"])
        self.assertIsNone(envelope["direction"])

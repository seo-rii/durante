import unittest

from replay import FEATURES, certificate, signed_row


class CertificateTests(unittest.TestCase):
    def test_native_equality_accepts_and_mixed_timeout_is_rejected(self):
        vector = [1] * len(FEATURES)
        self.assertEqual(signed_row(vector, vector, [5], [5], 1, "accept")["row"], [0] * len(FEATURES))
        for times in ([5], [5, 6]):
            with self.subTest(times=times), self.assertRaises(ValueError):
                signed_row(vector, vector, times, [5], 1, "timeout")
        with self.assertRaises(ValueError):
            signed_row(vector, [0] * len(FEATURES), [6], [5], 1, "timeout")

    def test_zero_sum_requires_a_strict_verdict_and_positive_multiplier(self):
        # A zero sum contradicts a strictly positive right side, even at all-zero weights.
        row = [1] * len(FEATURES)
        nonstrict = dict(row=row, expected="accept", multiplier=1)
        strict = dict(row=[-v for v in row], expected="timeout", multiplier=1)
        self.assertEqual(certificate([nonstrict, strict]), [0] * len(row))
        for change in ({"expected": "accept"}, {"multiplier": 0}, {"row": row}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                certificate([nonstrict, {**strict, **change}])

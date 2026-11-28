from fractions import Fraction
import unittest

import replay


class EvidenceTest(unittest.TestCase):
    def test_skip_equality_and_both_sides_of_the_ratio(self):
        one_percent = replay.skipped_fraction(dict(l1d_refs=99, l1d_skipped=1))
        self.assertEqual(one_percent, Fraction(1, 100))
        models = replay.clocks["CANDIDATES"]
        d = {"sample": {"1": dict.fromkeys(models, "timeout") | {"native": "accept"}}}
        for a, b in (([0], [one_percent]), ([0, one_percent], [0])):
            counts = replay.coverage(d, {"graph": ["ref", "sample"]}, {"ref": a, "sample": b})
            self.assertEqual(counts["ML4"], {"abstain": 1})
        issued = replay.coverage(d, {"graph": ["ref", "sample"]}, {"ref": [0], "sample": [Fraction(1, 101)]})
        self.assertEqual(issued["ML4"], {"issued_native_decisive": 1, "false_timeout": 1})

    def test_manifest(self):
        replay.registered()

    def test_published_results(self):
        self.assertEqual(replay.replay(), replay.read(replay.HERE / "results.json"))

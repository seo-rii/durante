from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from wall_locality.score import predict


class RangeClock(unittest.TestCase):
    def test_arm_specific_fractions_round_once(self):
        def row(branch, full_branch, ll, pages):
            return dict(ordinary=dict(instructions=140, rep_bytes=0), branch_only=dict(Bcm=branch),
                        full=dict(Bcm=full_branch, DLmr=ll), leaf_pages=dict(misses=pages))
        a = dict(row(10, 4, 80, 7), native_ns=[1000] * 9)
        b = dict(row(50, 12, 40, 7), native_ns=[1011] * 9)
        held = row(20, 10, 85, 3)
        self.assertEqual(predict(a, b, held), dict(ordinary=1000, midpoint=1006, branch_misses=1003,
                                                  ll_read_misses=1000, pages_only=1006,
                                                  branch_pages=1006, full_joint=1009))
        held["leaf_pages"]["misses"] = 53
        b["leaf_pages"]["misses"] = 18
        self.assertEqual(predict(a, b, held)["branch_pages"], 1011)
        with self.assertRaisesRegex(ValueError, "native label"):
            predict(a, b, dict(held, native_ns=[1001]))

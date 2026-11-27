from fractions import Fraction
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
import sys
import unittest

EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXPERIMENTS))
from contest_branch.score import predict, TRANSFER_NS_PER_MISS


class WallClock(unittest.TestCase):
    def test_transfer_penalty_reproduces_published_prior(self):
        slopes = []
        plan = json.loads((EXPERIMENTS / "contest_branch/plan.json").read_bytes())
        for name, digest in plan["scrivener_metrics"].items():
            raw = (EXPERIMENTS / "results" / name / "metrics.json").read_bytes()
            self.assertEqual(sha256(raw).hexdigest(), digest)
            cases = json.loads(raw)["cases"]
            for program, a in cases["ascending"].items():
                b = cases["shuffled"][program]
                slopes.append(Fraction(median(b["native_ns"]) - median(a["native_ns"]),
                                       b["cachegrind"]["Bcm"] - a["cachegrind"]["Bcm"]))
        self.assertEqual(len(slopes), 4)
        self.assertEqual(round(median(slopes)), TRANSFER_NS_PER_MISS)

    def test_independent_features_clamp_and_reject_native_label(self):
        def row(bcm, dlmr):
            return dict(ordinary=dict(instructions=100, rep_bytes=4),
                        full=dict(DLmr=dlmr), branch_only=dict(Bcm=bcm))
        a = dict(row(2, 14), native_ns=[300] * 9)
        b = dict(row(6, 10), native_ns=[305] * 9)
        held = row(3, 18)
        self.assertEqual(predict(a, b, held), dict(ordinary=300, midpoint=303, branch_misses=302,
                                                  ll_read_misses=300, transferred_branch=446))
        with self.assertRaisesRegex(ValueError, "native label"):
            predict(a, b, dict(held, native_ns=[420]))

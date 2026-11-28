from fractions import Fraction
import unittest

import replay


class BranchTransfer(unittest.TestCase):
    def test_receipts_and_summary(self):
        result = replay.replay()
        self.assertEqual(result, replay.read(replay.HERE / "results.json"))
        for phase in ("primary","repeat","pooled"):
            for panel in ("predecessor","inversions"):
                for model, cells in result[phase][panel]["outcomes"].items():
                    with self.subTest(phase=phase, panel=panel, model=model):
                        self.assertEqual(sum(cells.values()),20)
            self.assertEqual(len(result[phase]["telemetry"]),12)

    def test_symmetric_anchor_and_absolute_units(self):
        # At a 1x limit fewer misses can override a larger original score.
        self.assertEqual(replay.ratio([20,30],[10,20]),(Fraction(1),Fraction(3)))
        self.assertEqual(replay.milan.relative_verdict([10],[10],1),"accept")
        reg, plan, branch = replay.registered()
        self.assertEqual(branch["gshare_mispredicts"],Fraction("6.92658480142496"))
        self.assertEqual(reg["expected_rows"],{".":144})
        self.assertEqual(plan["protocol"]["groups"]["runs"][0],"runs-merge")


if __name__ == "__main__":
    unittest.main()

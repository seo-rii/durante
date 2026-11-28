from fractions import Fraction
import json
from pathlib import Path
import unittest

import replay


class GapTests(unittest.TestCase):
    def test_unequal_denominators_and_zero_column(self):
        bound,key = replay.ceiling({"a":[2,4], "b":[10], "unused":[0]},
                                   {"a":[1,5], "b":[12], "unused":[0]})
        self.assertEqual((bound,key),(Fraction(5,2),"a"))
        self.assertEqual(replay.ceiling({"uncovered":[0,1]}, {"uncovered":[2]}),(None,"uncovered"))

    def test_favorable_skipped_weight_equality_is_acceptance(self):
        a,s = Fraction(5),Fraction(7)
        b = replay.constraint(a,s,1,5,2)
        self.assertEqual(s + b*5, 2*(a+b))

    def test_original_and_independent_replays(self):
        result = replay.main()
        self.assertEqual(result,json.loads((Path(__file__).parent/"results.json").read_bytes()))
        self.assertLess(result["all_feature_linear_ceiling"]["ratio"],2)
        self.assertGreater(result["per_boot"]["primary"]["set_over_intended"]["native_all_pairs"][0],2)
        self.assertLess(result["per_boot"]["repeat"]["set_over_intended"]["native_all_pairs"][0],2)
        self.assertGreater(result["per_boot"]["repeat"]["set_over_intended"]["native_all_pairs"][1],2)
        bounds = result["skipped_charge"]
        self.assertGreater(Fraction(*bounds["lower_exclusive"]["seconds_per_skipped"]),
                           Fraction(*bounds["upper_inclusive"]["seconds_per_skipped"]))
        self.assertLess(result["independent_graph"]["native_set_over_own_heap"][1],2)
        fit = result["historical_residual_fit"]["transfer"]
        self.assertEqual(fit["primary"]["outcomes"], {"agree":15, "false_accept":1, "false_timeout":1, "indeterminate":1})
        self.assertEqual(fit["pooled"]["errors"], [{"name":"bfs_grid--list_queue","factor":2,"outcome":"false_timeout"}])


if __name__ == "__main__":
    unittest.main()

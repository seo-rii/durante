import copy
import unittest

from replay import compare, different


def measurement(cpu, **fields):
    return dict(cpu_us=cpu, counters=dict(instructions=100, **fields))


class JointCollectionTests(unittest.TestCase):
    def setUp(self):
        full = measurement(11, misses=3)
        branch = measurement(13)
        branch["predictor"] = {"mispredicts": 2, "loads": 9}
        both = dict(full, cpu_us=19, predictor=branch["predictor"].copy())
        self.arms = {"flat":[full], "branch":[branch], "joint":[both], "native":[measurement(1)]}
        self.old = copy.deepcopy(dict(flat=[full], outer=[full], branch=[branch]))

    def test_compares_every_counter_and_sums_separate_medians(self):
        result = compare(self.arms, self.old)
        self.assertTrue(result["counters_match"])
        self.assertEqual(result["joint_over_separate"], "19/24")
        self.assertTrue(result["faster"])
        self.arms["joint"][0]["cpu_us"] = 24
        self.assertFalse(compare(self.arms, self.old)["faster"])

    def test_work_predictor_and_historical_drift_are_visible(self):
        self.arms["joint"][0]["predictor"]["loads"] += 1
        self.old["outer"][0]["counters"]["misses"] += 1
        result = compare(self.arms, self.old)
        self.assertFalse(result["counters_match"])
        self.assertEqual(result["different_fields"]["predictor"], ["loads"])
        self.assertEqual(result["different_fields"]["historical_cache"], ["misses"])
        self.assertEqual(different([{"counter":0}, {}]), ["counter"])

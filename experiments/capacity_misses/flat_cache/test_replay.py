import unittest

import replay


class FlatCacheTests(unittest.TestCase):
    def test_change_outside_fitted_features_is_visible(self):
        outer = (4096, 32768, 524288, 4, 3, 3, 2, 2, 1, 1, 1, 1, 1, 1)
        counters = dict(instructions=100, movs_bytes=1, stos_bytes=2, latency_x16=500,
                        l1d_misses=4, l1d_skipped=3, outer=outer)
        features = replay.ml4(counters, replay.read(replay.GRAPH / "frozen.json")["ml4"])
        arms = {"native": [dict(cpu_us=10, counters={})],
                "outer": [dict(cpu_us=100, counters=counters), dict(cpu_us=120, counters=counters)],
                "flat_outer": [dict(cpu_us=40, counters={**counters, "l1d_skipped": 4}),
                               dict(cpu_us=50, counters=counters)]}
        report = replay.summarize(arms, features)
        self.assertEqual(report["different_counter_fields"], ["l1d_skipped"])
        self.assertTrue(report["all_eight_ml4_equal"])
        self.assertEqual(report["all_pair_speedup"], [2, 3])

    def test_registered_comparison(self):
        result = replay.replay()
        self.assertEqual(result, replay.read(replay.HERE / "results.json"))
        self.assertTrue(result["counter_equivalent"])
        for case in result["pooled"].values():
            self.assertTrue(case["original_matches_earlier_graph"])


if __name__ == "__main__":
    unittest.main()

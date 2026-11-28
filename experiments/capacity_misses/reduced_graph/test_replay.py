import unittest

import counters
import replay


class ReplayTests(unittest.TestCase):
    def test_published_runs(self):
        result = replay.replay()
        self.assertEqual(result, replay.read(replay.HERE / "results.json"))
        for case in result["pooled"]["cases"].values():
            self.assertEqual(case["samples"], {"native": 10, "full": 4, "reduced": 4})
            self.assertTrue(case["retained_and_skipped_identical"])

    def test_one_mismatching_counter_or_skipped_operand_is_detected(self):
        plan = {"reference": "ref", "ml4": dict.fromkeys(counters.KEEP, 1), "deadline_us": 65}
        initial = dict.fromkeys(counters.KEEP, 10)
        reduced = dict(cpu_us=100, features=initial.copy(), skipped=2)
        samples = {"ref": {"native": [{"cpu_us": 60}],
                           "full": [dict(cpu_us=120, features=initial, all_features=initial, skipped=2)],
                           "reduced": [reduced]}}
        def verdict():
            return replay.summarize(samples, plan)["cases"]["ref"]["retained_and_skipped_identical"]
        self.assertTrue(verdict())
        reduced["features"]["dependent_2m"] += 1
        self.assertFalse(verdict())
        reduced["features"] = initial.copy()
        reduced["skipped"] += 1
        self.assertFalse(verdict())

    def test_all_observed_ratios_and_inclusive_deadline(self):
        self.assertEqual(replay.ratio_interval([10, 15], [5, 8]), [1.25, 3])
        self.assertEqual(replay.label([64, 65], 65), "accept")
        self.assertEqual(replay.label([65, 66], 65), "indeterminate")
        self.assertEqual(replay.label([66, 70], 65), "timeout")


if __name__ == "__main__":
    unittest.main()

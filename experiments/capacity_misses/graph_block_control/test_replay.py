import unittest

import replay
from graph_evidence import read_boot


class ControlTests(unittest.TestCase):
    def test_published_result(self):
        report = replay.replay()
        self.assertEqual(report, replay.read(replay.HERE / "results.json"))
        for name, case in report["pooled"].items():
            with self.subTest(name=name):
                self.assertEqual(case["samples"], dict.fromkeys(replay.MODES, 10))
                self.assertEqual(set(case["distinct_readings_per_counter"].values()), {1})
                self.assertIn("latency_x16", case["distinct_readings_per_counter"])

    def test_different_installed_client_is_rejected(self):
        reg, plan = replay.registered()
        host = replay.read(replay.HERE / "evidence/cloud.json")["primary"]
        wrong = {a:plan["clients"]["original.so"] for a in replay.MODES}
        with self.assertRaisesRegex(ValueError, "incorrect installed diagnostic"):
            read_boot("primary", host, reg, plan, evidence=replay.HERE/"evidence",
                      freeze=replay.FREEZE, client_by_arm=wrong)

    def test_both_differences_preserve_sign_and_all_pair_ranges(self):
        cpus = {"latency": [6, 10], "callback": [4, 6], "l1d": [17, 19]}
        rows = {a:[dict(cpu_us=t, counters={"instructions": 25}) for t in times] for a,times in cpus.items()}
        v = replay.summarize(rows)
        self.assertEqual(v["changes"]["callback"], {"median_delta_us":-3, "all_pair_delta_us":[-6, 0],
                                                    "percent_l1d_minus_latency":-30})
        self.assertEqual(v["changes"]["l1d"], {"median_delta_us":13, "all_pair_delta_us":[11, 15],
                                               "percent_l1d_minus_latency":130})


if __name__ == "__main__":
    unittest.main()

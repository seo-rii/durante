import unittest

from replay import relative_verdict, replay


class MilanVerdicts(unittest.TestCase):
    def test_native_deadline_includes_equality_and_both_samples_matter(self):
        self.assertEqual(relative_verdict([18, 20], [10, 11], 2), "accept")
        self.assertEqual(relative_verdict([21, 23], [10, 11], 2), "indeterminate")
        self.assertEqual(relative_verdict([22, 23], [10, 11], 2), "indeterminate")
        self.assertEqual(relative_verdict([23, 26], [10, 11], 2), "timeout")

    def test_pooled_result_retains_all_cases_and_the_primary_false_accept(self):
        results = replay()
        self.assertTrue(all(results["exact_across_all_four"].values()))
        self.assertEqual(len(results["exact_across_all_four"]), 9)
        for model in ("M0", "M2", "M4", "ML4"):
            self.assertEqual(sum(results["combined_outcomes"][model].values()), 18)
        self.assertEqual(results["combined_outcomes"]["ML4"], {"agree": 12, "indeterminate": 6})
        decisions = results["primary"]["decisions"]
        cell, = [d for d in decisions if d["case"] == "dijkstra--set_decrease_key" and d["factor"] == 2]
        self.assertEqual((cell["native"], cell["clock"]["ML4"]), ("timeout", "accept"))

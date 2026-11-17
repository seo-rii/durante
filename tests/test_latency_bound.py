"""Hand-derived expectations of the static latency-bound fixtures."""
import re
import unittest

import latency_bound


class LatencyBoundExpectationTests(unittest.TestCase):
    def test_non_loop_blocks_are_issue_bound(self):
        # Entry and exit blocks have no self-loop, so they cost 4/16 cycle each.
        for _, entry_n, entry_b, body_n, body_b in latency_bound.KERNELS.values():
            self.assertEqual(entry_b, 4 * entry_n)
            self.assertGreaterEqual(body_b, 4 * body_n)
        self.assertEqual(latency_bound.EXIT_BOUND, 4 * latency_bound.EXIT_INSTRUCTIONS)

    def test_recurrences_follow_the_latency_table(self):
        cycles = {mode: body_b / 16 for mode, (_, _, _, _, body_b) in latency_bound.KERNELS.items()}
        self.assertEqual(cycles, {0: 2 * 3, 1: 4 + 1, 2: 8 / 4, 3: 1 + 20, 4: 2 * 1})

    def test_totals_are_exact_and_linear_in_iterations(self):
        self.assertEqual(latency_bound.expected(0, 1000), {"instructions": 4005, "latency_bound_x16": 95940})
        for mode, (_, _, _, body_n, body_b) in latency_bound.KERNELS.items():
            low, high = (latency_bound.expected(mode, n) for n in latency_bound.ITERATIONS)
            span = latency_bound.ITERATIONS[1] - latency_bound.ITERATIONS[0]
            self.assertEqual(high["instructions"] - low["instructions"], body_n * span)
            self.assertEqual(high["latency_bound_x16"] - low["latency_bound_x16"], body_b * span)

    def test_every_fixture_mode_is_checked(self):
        source = (latency_bound.ROOT / "tests/latency_bound/kernels.S").read_text()
        modes = {int(value) for value in re.findall(r"#(?:el)?if MODE == (\d+)", source)}
        self.assertEqual(modes, set(latency_bound.KERNELS))


if __name__ == "__main__":
    unittest.main()

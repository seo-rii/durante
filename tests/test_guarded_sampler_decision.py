from pathlib import Path
import runpy
import unittest

decision = runpy.run_path(str(Path(__file__).resolve().parents[1] / "experiments/guarded_sampler/guard.py"))["decision"]


def reports(visited, omitted):
    pairs = visited // 1024 + (visited % 1024 >= 2)
    counts = dict(loads=visited, pairs=pairs, near_pairs=0, far_pairs=pairs)
    sites = [dict(offset=96, sampled_address_span_bytes=128 if pairs else 0, **counts)] if visited else []
    sample = dict(pid=77, schema=2, threads=1, line_bytes=64, period=1024, sites=sites, **counts)
    guard = dict(schema=1, pid=77, threads=1, arm="guarded", scope="main_module_eligible_scalar_reads",
                 omitted_loads=omitted)
    return sample, guard


class GuardDecision(unittest.TestCase):
    def test_threshold_and_empty_paths(self):
        for selected, omitted, expected in ((9, 1, "use_profiled"), (89, 11, "run_full"),
                                            (0, 10, "run_full"), (0, 0, "run_full"), (9, 0, "use_profiled")):
            with self.subTest(selected=selected, omitted=omitted):
                s, g = reports(selected, omitted)
                result = decision(s, g, 77)
                self.assertEqual(result["status"], expected)
                self.assertEqual(result["total_visits"], selected + omitted)

    def test_incomplete_wrong_pid_and_overflow(self):
        s, g = reports(1, (1 << 64) - 1)
        for a, b in ((s, g), (s, {**g, "pid": 78}), (s, {**g, "omitted_loads": False}),
                     ({**s, "loads": 2}, {**g, "omitted_loads": 0})):
            with self.assertRaises(ValueError):
                decision(a, b, 77)

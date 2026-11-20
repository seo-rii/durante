"""The paired estimator must cancel run-order effects and preserve the clock work."""
from copy import deepcopy
import sys
from pathlib import Path
import unittest

EXP = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXP))
from summarize_live_pairs import summarize


def paired_report():
    work = dict(valid=True, quality="uninterrupted", app_exit_seen=True, app_exit_code=0,
                accounting_error=False, signals_observed=0, threads_complete=True,
                threads_started=1, threads_finished=1, instructions=42)
    rows = []
    for index, arms in enumerate([("baseline", "candidate"), ("candidate", "baseline")], 1):
        for position, arm in enumerate(arms):
            # The second position costs twice as much. The candidate itself costs 20% less.
            time = (1 + position) * (0.8 if arm == "candidate" else 1.0)
            rows.append(dict(name="short", round=index, mode=arm, cache=None, work=dict(work),
                             cpu_s=time, wall_s=time * 3))
    return dict(mode="ordinary-live", rounds=2, clients=dict(baseline="a", candidate="b"),
                cases={"short": {}}, cpu=0, drrun_sha256="d", samples=rows)


class PairedRoundsTests(unittest.TestCase):
    def test_cancels_order_effect_in_both_clocks(self):
        case = summarize(paired_report())["cases"]["short"]
        for clock in ("cpu", "wall"):
            self.assertAlmostEqual(case[clock]["median_change_percent"], -20)
            self.assertEqual(case[clock]["faster_blocks"], 1)
        self.assertEqual(case["rounds"][1]["first_arm"], "candidate")
        self.assertEqual(case["rounds"][1]["cpu_ns"], [2_000_000_000, 800_000_000])

    def test_incomplete_and_unbalanced_rounds(self):
        report = paired_report()
        for samples in (report["samples"][:-1], report["samples"][:2] * 2,
                        report["samples"][:2] + report["samples"][2:][::-1]):
            with self.subTest(samples=samples), self.assertRaises(ValueError):
                summarize({**report, "samples": samples})

    def test_rejects_invalid_time_and_different_work(self):
        report = paired_report()
        for key, value in (("cpu_s", float("inf")), ("wall_s", 0), ("cache", {}),
                           ("work", {**report["samples"][0]["work"], "instructions": 43})):
            invalid = deepcopy(report)
            invalid["samples"][1][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                summarize(invalid)

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


class PublishedPairsTests(unittest.TestCase):
    def test_receipts_and_held_out_identities(self):
        from hashlib import sha256
        import json
        from summarize_live_pairs import effect

        results = EXP / "results/cpp20-static-tls"
        source = EXP / "static_input_transfer"
        manifest = json.loads((source / "manifest.json").read_bytes())
        self.assertEqual(sha256((source / "main.cpp").read_bytes()).hexdigest(), manifest["source_sha256"])
        self.assertEqual(sha256((source / "manifest.json").read_bytes()).hexdigest(),
                         "85dd9d336934b7711cc5fb48e15942391cbdd11aa64e27f9608bd801b195e031")
        self.assertEqual(sha256((results / "protocol.txt").read_bytes()).hexdigest(),
                         "b050567432c534dd5974d467536d3db53a86a1494874361221dd586c1cf039c5")
        base = json.loads((results / "identical.json").read_bytes())
        main = json.loads((results / "local.json").read_bytes())
        self.assertEqual(base["clients"]["baseline"], base["clients"]["candidate"])
        self.assertEqual(base["clients"]["baseline"], main["clients"]["baseline"])
        self.assertNotEqual(main["clients"]["baseline"], main["clients"]["candidate"])
        for name, identity in manifest["cases"].items():
            case = main["cases"][name]
            self.assertEqual(case["elf_sha256"], manifest["elf_sha256"])
            for field, file in (("stdin_sha256", "in"), ("stdout_sha256", "oracle")):
                self.assertEqual(case[field], identity[field])
                self.assertEqual(case[field], sha256((source / name / file).read_bytes()).hexdigest())
        for report, case_count in ((base, 3), (main, 14)):
            self.assertEqual(report["time_columns"], ["baseline", "candidate"])
            self.assertEqual(len(report["cases"]), case_count)
            for case in report["cases"].values():
                self.assertEqual(len(case["rounds"]), 16)
                for clock in ("cpu", "wall"):
                    computed = effect(case["rounds"], clock + "_ns")
                    self.assertEqual(computed["faster_blocks"], case[clock]["faster_blocks"])
                    self.assertEqual(computed["blocks"], case[clock]["blocks"])
                    self.assertAlmostEqual(computed["median_change_percent"], case[clock]["median_change_percent"])

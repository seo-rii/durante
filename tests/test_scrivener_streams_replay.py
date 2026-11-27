"""Cross-check the registered stream forecasts against both original guest journals."""
from collections import Counter, defaultdict
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from statistics import median
import sys
from tempfile import TemporaryDirectory
import unittest

from test_scrivener_order_replay import load, unpack

EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXPERIMENTS))
from scrivener_streams.verify import verify


class ScrivenerStreamsReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.plan_path = EXPERIMENTS / "scrivener_streams/plan.json"
        cls.plan = load(cls.plan_path)
        cls.boots = []
        for suffix in ("", "-repeat"):
            directory = EXPERIMENTS / "results" / ("gce-scrivener-streams" + suffix)
            output = unpack(directory / "output.tar.gz", Path(temp.name) / directory.name)
            rows = load(output / "run.json")["rows"]
            native = defaultdict(list)
            for row in rows:
                if row["arm"] == "native":
                    native[row["name"], row["program"]].append(1000 * (row["user_us"] + row["sys_us"]))
            cls.boots.append((directory, rows, native, load(output / "predictions.json"), verify(output)))

    def test_replay_public_archives_and_seal(self):
        freezes, boots = [], []
        for directory, rows, _, seal, report in self.boots:
            self.assertEqual(report, load(directory / "metrics.json"))
            receipt = load(directory / "collection.json")
            self.assertEqual(receipt["output_sha256"], sha256((directory / "output.tar.gz").read_bytes()).hexdigest())
            self.assertEqual(receipt["freeze"]["plan_sha256"], sha256(self.plan_path.read_bytes()).hexdigest())
            platform, cleanup = receipt["platform"], receipt["cleanup"]
            self.assertEqual((platform["cpu_platform"], platform["machine_type"], platform["threads_per_core"]),
                             ("AMD Milan", "n2d-standard-4", 1))
            self.assertEqual((cleanup["instance_http"], cleanup["boot_disk_http"], cleanup["key_removed"]),
                             (404, 404, True))
            self.assertEqual(Counter(r["arm"] for r in rows), dict(scan=27, ordinary=36, branch=36, native=162))
            self.assertEqual((len(rows), report["verified_pids"], report["sealed_prefix_rows"]), (261, 261, 135))
            self.assertTrue(all(r["arm"] != "native" or r["name"] in self.plan["anchors"] for r in rows[:135]))
            self.assertTrue(all(r["arm"] == "native" and r["name"] not in self.plan["anchors"] for r in rows[135:]))
            self.assertEqual(set(seal["cases"]), {c["name"] for c in self.plan["cases"] if c["group"] != "anchor"})
            freezes.append(receipt["freeze"])
            boots.append(report["boot_sha256"])
        self.assertEqual(*freezes)
        self.assertNotEqual(*boots)

    def test_fresh_deadlines_and_paired_gates(self):
        names = [case["name"] for case in self.plan["cases"] if case["group"] == "holdout"]
        self.assertEqual(len(names), 6)
        for _, _, native, seal, report in self.boots:
            mistakes, worst = {}, {}
            for method, summary in report["summary"].items():
                wrong = {"false_pass": set(), "false_timeout": set()}
                majority_passes = majority_timeouts = mixed = count = 0
                errors = []
                for name in names:
                    for program in self.plan["binaries"]:
                        estimates = seal["cases"][name][program]
                        times = native[name, program]
                        self.assertEqual(len(times), 9)
                        actual = median(times)
                        predicted = estimates[method]
                        errors.append(abs(Fraction(predicted - actual, actual)))
                        for ms in self.plan["fixed_limits_ms"]:
                            limit = ms * 1_000_000
                            count += 1
                            majority_passes += predicted <= limit < actual
                            majority_timeouts += actual <= limit < predicted
                            key = (name, program, ms)
                            if min(times) <= limit < max(times):
                                mixed += 1
                            elif predicted <= limit < min(times):
                                wrong["false_pass"].add(key)
                            elif max(times) <= limit < predicted:
                                wrong["false_timeout"].add(key)
                expected = dict(cases=12, fixed_limits=144, mixed_limits=mixed,
                                within_20pct=sum(e <= Fraction(1, 5) for e in errors),
                                median_absolute_error_ppm=round(median(errors) * 1_000_000),
                                worst_absolute_error_ppm=round(max(errors) * 1_000_000),
                                unanimous_false_pass=len(wrong["false_pass"]),
                                unanimous_false_timeout=len(wrong["false_timeout"]),
                                unanimous_correct=count - mixed - sum(map(len, wrong.values())),
                                majority_correct=count - majority_passes - majority_timeouts,
                                majority_false_pass=majority_passes, majority_false_timeout=majority_timeouts)
                self.assertEqual(count, 144)
                self.assertEqual(summary, expected)
                mistakes[method], worst[method] = wrong, max(errors)

            for old, new in (("pages", "stream"), ("branch_pages", "branch_stream")):
                comparison = report["qualification"][new]
                expected = {}
                for error in ("false_pass", "false_timeout"):
                    expected[error + "_fixed"] = mistakes[old][error] - mistakes[new][error]
                    expected[error + "_added"] = mistakes[new][error] - mistakes[old][error]
                for key, values in expected.items():
                    self.assertCountEqual(map(tuple, comparison["unanimous_error_changes"][key]), values)
                self.assertEqual(comparison["accuracy_gate"], worst[new] < worst[old])
                fixes = expected["false_pass_fixed"] | expected["false_timeout_fixed"]
                added = expected["false_pass_added"] | expected["false_timeout_added"]
                self.assertEqual(comparison["deadline_gate"], bool(fixes) and not added)

    def test_65ms_control_against_native_observations_and_sealed_predictions(self):
        for _, _, native, seal, report in self.boots:
            self.assertNotIn("lanes32", report["cases"])
            self.assertEqual(set(report["controls"]), {"lanes32"})
            for program in self.plan["binaries"]:
                control = report["controls"]["lanes32"][program]
                times = native["lanes32", program]
                self.assertEqual(len(times), 9)
                self.assertEqual(control["native_tle_votes"]["65"], sum(t > 65_000_000 for t in times))
                self.assertEqual(control["native_ns"], times)
                self.assertEqual(control["predictions_ns"], seal["cases"]["lanes32"][program])

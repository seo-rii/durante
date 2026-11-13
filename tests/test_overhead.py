"""Overhead validation policy and actual per-child resource/capture regressions."""
import copy
import importlib.util
from pathlib import Path
import signal
import sys
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "overhead", Path(__file__).resolve().parents[1] / "experiments/overhead.py")
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


def report(fixture="startup", mode="counter_rep_off"):
    enabled = mode == "counter_rep_on"
    return {"schema": 1, "authority": "diagnostic_only", "integrity_verified": False,
            "valid": True, "complete": True, "quality": "uninterrupted",
            "metric": "bb_entry_app_instructions", "scope": "process_tree_user_code",
            "rep_bytes_enabled": enabled, "process_count": 1, "image_count": 1,
            "instructions": {"startup": 8, "loop": 4_000_011, "rep": 1292}[fixture],
            "signals_observed": 0, "unsupported_rep_entries": 0,
            "rep_entries": 256 if enabled and fixture == "rep" else 0,
            "rep_bytes": 16_777_216 if enabled and fixture == "rep" else 0,
            "rep_telemetry_status": "movs_stos_only" if enabled else "disabled",
            "measurement_contract": bench.collector.measurement_contract()}


def matrix():
    rows = []
    for fixture in bench.FIXTURES:
        for position, mode in enumerate(bench.MODES):
            for phase, count in (("warmup", 1), ("sample", 2)):
                for index in range(count):
                    value = 1000 if phase == "warmup" else (index + 1) * (position + 1)
                    rows.append({"fixture": fixture, "mode": mode, "phase": phase,
                                 "index": index, "passed": True,
                                 "metrics": {key: value for key in bench.METRICS},
                                 "measurement": report(fixture, mode)})
    return rows


class OverheadPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="durante-overhead-tests-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.timer = cls.root / "timer"
        bench.tool_output(["gcc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                           str(bench.ROOT / "experiments/overhead_timer.c"), "-o", str(cls.timer)])
        cls.libc, cls.previous = bench.own_subreaper()
        cls.addClassCleanup(cls.libc.prctl, 36, cls.previous, 0, 0, 0)
        cls.addClassCleanup(bench.cleanup_orphans)

    def sample(self, name, command, timeout=5):
        return bench.run_sample(self.timer, self.root / (name + ".json"), command, timeout)

    def test_oracles_cover_every_fixed_fixture_and_rep_mode(self):
        for fixture in bench.FIXTURES:
            for mode in ("counter_rep_off", "counter_rep_on"):
                self.assertIsNone(bench.measurement_error(report(fixture, mode), fixture, mode))

    def test_missing_partial_or_changed_work_never_qualifies(self):
        for key, value in (("valid", False), ("complete", False), ("instructions", 7),
                           ("instructions", True), ("rep_bytes", 1), ("rep_entries", 1),
                           ("signals_observed", 1), ("unsupported_rep_entries", 1),
                           ("image_count", 2), ("process_count", 2),
                           ("integrity_verified", True), ("authority", "trusted"),
                           ("measurement_contract", {}), ("rep_bytes_enabled", 0)):
            with self.subTest(key=key):
                row = report()
                row[key] = value
                self.assertIsNotNone(bench.measurement_error(row, "startup", "counter_rep_off"))
        for key in ("rep_bytes", "rep_entries", "instructions"):
            row = report("rep", "counter_rep_on")
            row[key] -= 1
            self.assertIsNotNone(bench.measurement_error(row, "rep", "counter_rep_on"))

    def test_matrix_rejects_missing_duplicate_failure_and_unknown_mode(self):
        rows = matrix()
        self.assertIsNone(bench.inspect_matrix(rows, 1, 2))
        self.assertIsNotNone(bench.inspect_matrix(rows[:-1], 1, 2))
        self.assertIsNotNone(bench.inspect_matrix(rows + [rows[0]], 1, 2))
        for field, value in (("passed", False), ("mode", "arbitrary-command"), ("index", 3)):
            changed = copy.deepcopy(rows)
            changed[0][field] = value
            self.assertIsNotNone(bench.inspect_matrix(changed, 1, 2))

    def test_warmups_excluded_and_zero_baselines_do_not_create_ratios(self):
        rows = matrix()
        for row in rows:
            if row["mode"] == "native":
                row["metrics"]["user_seconds"] = 0
        result = bench.summarize(rows)["startup"]
        groups = result["distributions"]
        self.assertEqual(groups["native"]["wall_seconds"]["samples"], [1, 2])
        self.assertEqual(groups["native"]["wall_seconds"]["median"], 1.5)
        self.assertEqual(groups["counter_rep_on"]["instructions"]["range"], 0)
        self.assertEqual(groups["counter_rep_on"]["instructions"]["distinct"], 1)
        ratios = result["median_ratios_to_native"]["engine"]
        self.assertEqual(ratios["wall_seconds"], 2)
        self.assertIsNone(ratios["user_seconds"])

    def test_per_child_rss_can_fall_after_a_larger_child(self):
        # RUSAGE_CHILDREN.ru_maxrss is a high-water mark; subtracting cumulative
        # snapshots would yield zero here rather than this child's own RSS.
        large = self.sample("large", [sys.executable, "-I", "-c", "x=bytearray(64*1024*1024); print(len(x))"])
        small = self.sample("small", [sys.executable, "-I", "-c", "print('small')"])
        self.assertIsNone(bench.process_error(large))
        self.assertIsNone(bench.process_error(small))
        self.assertGreater(small["metrics"]["peak_rss_kib"], 0)
        self.assertLess(small["metrics"]["peak_rss_kib"], large["metrics"]["peak_rss_kib"])
        self.assertGreaterEqual(small["metrics"]["cpu_seconds"], 0)

    def test_timer_deadline_owns_child_and_refuses_existing_report(self):
        row = self.sample("timer-deadline", [sys.executable, "-I", "-c", "import time; time.sleep(60)"], timeout=0.1)
        self.assertTrue(row["timed_out"])
        self.assertTrue(row["reaped"])
        self.assertEqual(row["exit_code"], -signal.SIGKILL)
        self.assertIsNotNone(bench.process_error(row))
        original = (self.root / "timer-deadline.json").read_bytes()
        sentinel = self.root / "must-not-run"
        repeated = self.sample("timer-deadline", [sys.executable, "-I", "-c",
                              "from pathlib import Path; Path(" + repr(str(sentinel)) + ").touch()"])
        self.assertIsNotNone(bench.process_error(repeated))
        self.assertEqual((self.root / "timer-deadline.json").read_bytes(), original)
        self.assertFalse(sentinel.exists())

    def test_outer_output_limit_cleans_adopted_fixture(self):
        row = self.sample("timer-flood", [sys.executable, "-I", "-c",
                          "import os\nwhile True: os.write(1,b'x'*65536)"])
        self.assertTrue(row["output_limit"])
        self.assertIsNotNone(bench.process_error(row))
        self.assertTrue(bench.cleanup_orphans())
        self.assertEqual(Path(f"/proc/self/task/{bench.os.getpid()}/children").read_text().strip(), "")

    def test_changed_inputs_or_failed_cleanup_remove_statistics(self):
        good = {"rows": matrix(), "warmups": 1, "repeats": 2,
                "inputs_before": {"core": "original"}, "inputs_after": {"core": "original"},
                "executables_before": {"fixture": "same"}, "executables_after": {"fixture": "same"},
                "cleanup_complete": True}
        bench.finalize(good)
        self.assertTrue(good["passed"])
        self.assertTrue(good["statistics"])
        for key, value in (("inputs_after", {"core": "changed"}),
                           ("executables_after", {"fixture": "changed"}),
                           ("cleanup_complete", False), ("rows", matrix()[:-1])):
            with self.subTest(key=key):
                changed = copy.deepcopy(good)
                changed[key] = value
                bench.finalize(changed)
                self.assertFalse(changed["passed"])
                self.assertEqual(changed["statistics"], {})

    def test_timeout_and_flood_are_bounded_and_reaped(self):
        sleeping = bench.run_timed([sys.executable, "-I", "-c", "import time; time.sleep(60)"], timeout=0.1)
        self.assertTrue(sleeping["timed_out"])
        self.assertTrue(sleeping["reaped"])
        self.assertEqual(sleeping["exit_code"], -signal.SIGKILL)
        self.assertIsNotNone(bench.process_error(sleeping))
        flood = bench.run_timed([sys.executable, "-I", "-c", "import os\nwhile True: os.write(2,b'x'*65536)"])
        self.assertTrue(flood["output_limit"])
        self.assertTrue(flood["reaped"])
        self.assertEqual(len(flood["stderr"]), bench.MAX_CAPTURE)
        self.assertIsNotNone(bench.process_error(flood))

    def test_crash_invalid_resources_and_launch_errors_are_failures(self):
        crashed = bench.run_timed([sys.executable, "-I", "-c",
                                  "import os,signal; os.kill(os.getpid(),signal.SIGKILL)"])
        self.assertTrue(crashed["reaped"])
        self.assertEqual(crashed["exit_code"], -signal.SIGKILL)
        self.assertIsNotNone(bench.process_error(crashed))
        missing = bench.run_timed(["/definitely/nonexistent/durante-overhead-fixture"])
        self.assertIsNotNone(missing["error"])
        self.assertIsNotNone(bench.process_error(missing))
        good = bench.run_timed([sys.executable, "-I", "-c", "pass"])
        for value in (-1, float("nan"), float("inf"), True, None):
            row = copy.deepcopy(good)
            row["metrics"]["peak_rss_kib"] = value
            self.assertIsNotNone(bench.process_error(row))


if __name__ == "__main__":
    unittest.main()

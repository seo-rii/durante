"""Check the fixed observation policy and execute the unchanged native Java probe."""
from contextlib import ExitStack, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import java_tiered as tiered


class JavaTieredTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="durante-tiered-unit-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.args = SimpleNamespace(sdk=self.root / "sdk", output_dir=self.root,
                                    repeats=3, prlimit=tiered.executable_path("prlimit"))

    @staticmethod
    def success():
        return {"exit_code": 0, "root_pid": 123, "timeout": False,
                "infrastructure_ok": True, "cleanup_complete": True,
                "stdout": tiered.EXPECTED, "stderr": "", "errors": []}

    def plan(self):
        return tiered.observation_plan(self.args, ["/selected/java", *tiered.JAVA_FLAGS,
                                                   "-cp", str(self.root / "classes"), "RuntimeProbe"])

    def test_engine_failure_is_retained_and_never_retried_to_green(self):
        rows = self.plan()
        failed = {**self.success(), "exit_code": -6, "stdout": "JVM fatal error\n"}
        with patch.object(tiered, "run_process", side_effect=[self.success(), failed,
                                                             self.success(), self.success()]) as execute:
            tiered.run_observations(rows, self.args.prlimit, time.monotonic() + 10)
        self.assertEqual(execute.call_count, 4)
        self.assertEqual([row["passed"] for row in rows], [True, False, True, True])
        self.assertTrue(tiered.observation_errors(rows, 3))
        self.assertEqual(len({call.args[1] for call in execute.call_args_list}), 4)
        for call in execute.call_args_list:
            self.assertTrue(call.args[1].is_dir())
            self.assertLessEqual(call.args[2], 40000)
        self.assertEqual(rows[1]["command"][:9], [str(self.args.sdk / "bin64/drrun"),
            "-quiet", "-follow_children", "-disable_traces", "-vm_size", "32M",
            "-vmheap_size", "32M", "--"])

    def test_failed_native_control_records_every_unrun_engine_observation(self):
        rows = self.plan()
        with patch.object(tiered, "run_process", return_value={**self.success(), "stdout": "wrong\n"}) as execute:
            tiered.run_observations(rows, self.args.prlimit, time.monotonic() + 10)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row["state"] == "incomplete" and not row["passed"] for row in rows[1:]))
        self.assertTrue(all("native control failed" in row["errors"] for row in rows[1:]))
        self.assertTrue(tiered.observation_errors(rows, 3))

    def test_budget_exhaustion_records_the_fixed_plan_without_spawning_more(self):
        rows = self.plan()
        with patch.object(tiered.time, "monotonic", side_effect=[0, 2, 2, 2]), \
                patch.object(tiered, "run_process", return_value=self.success()) as execute:
            tiered.run_observations(rows, self.args.prlimit, deadline=1)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(execute.call_args.args[2], 1000)
        self.assertTrue(all(row["state"] == "incomplete" for row in rows[1:]))
        self.assertTrue(all("campaign deadline expired" in row["errors"] for row in rows[1:]))
        self.assertTrue(tiered.observation_errors(rows, 3))

    def test_timeout_cleanup_failure_and_crash_artifact_cannot_pass(self):
        for label in ("timeout", "cleanup", "crash-report"):
            with self.subTest(failure=label):
                directory = self.root / label
                directory.mkdir()
                self.args.output_dir = directory
                rows = self.plan()

                def execute(command, work, timeout, prlimit):
                    result = self.success()
                    if work.name == "001-engine-only":
                        if label == "timeout":
                            result.update(timeout=True, infrastructure_ok=False)
                        elif label == "cleanup":
                            result.update(cleanup_complete=False, infrastructure_ok=False)
                        else:
                            (work / "hs_err_pid123.log").write_text("fatal JVM report\n")
                    return result

                with patch.object(tiered, "run_process", side_effect=execute):
                    tiered.run_observations(rows, self.args.prlimit, time.monotonic() + 10)
                self.assertFalse(rows[1]["passed"])
                self.assertTrue(tiered.observation_errors(rows, 3))
                if label == "cleanup":
                    self.assertTrue(all(row["state"] == "incomplete" for row in rows[2:]))
                if label == "crash-report":
                    self.assertEqual(rows[1]["hs_err_paths"],
                                     [str(directory / "001-engine-only/hs_err_pid123.log")])

    def test_missing_and_duplicate_observations_are_not_complete_success(self):
        rows = self.plan()
        with patch.object(tiered, "run_process", side_effect=lambda *args: self.success()):
            tiered.run_observations(rows, self.args.prlimit, time.monotonic() + 10)
        self.assertEqual(tiered.observation_errors(rows, 3), [])
        self.assertTrue(tiered.observation_errors(rows[:-1], 3))
        self.assertTrue(tiered.observation_errors(rows + [rows[-1]], 3))

    def test_missing_runtime_publishes_all_incomplete_observations(self):
        output = self.root / "result"
        with redirect_stdout(io.StringIO()), patch.object(tiered, "run_process") as execute:
            code = tiered.main(["--sdk", str(self.args.sdk), "--java", str(self.root / "missing-java"),
                                "--javac", str(self.root / "missing-javac"),
                                "--output-dir", str(output), "--repeats", "3"])
        report = json.loads((output / "results.json").read_text())
        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertEqual(report["planned_observations"], 4)
        self.assertEqual(report["completed_observations"], 0)
        self.assertTrue(all(row["errors"] for row in report["rows"]))
        execute.assert_not_called()

    def test_wrong_compiler_major_or_gc_fails_before_fixture_compilation(self):
        self.args.java, self.args.javac = "/selected/java", "/selected/javac"
        for label, runtime, compiler, expected_calls in (
                ("wrong-major", '-XX:+UseG1GC\nopenjdk version "17.0.21"', "javac 21.0.1", 2),
                ("wrong-gc", '-XX:+UseSerialGC\nopenjdk version "17.0.21"', "javac 17.0.21", 1)):
            with self.subTest(failure=label):
                self.args.output_dir = self.root / label
                self.args.output_dir.mkdir()
                outcomes = [{**self.success(), "stdout": runtime}, {**self.success(), "stdout": compiler}]
                with patch.object(tiered, "run_process", side_effect=outcomes) as execute:
                    with self.assertRaises(ValueError):
                        tiered.probe_and_compile(self.args, tiered.InputProvenance({}),
                                                  time.monotonic() + 10, [])
                self.assertEqual(execute.call_count, expected_calls)
                self.assertFalse((self.args.output_dir / "classes").exists())

    def test_changed_jvm_invalidates_success_and_restores_actual_cpu_affinity(self):
        artifacts = self.root / "artifacts"
        java_home, sdk = artifacts / "jdk", artifacts / "sdk"
        for path in (java_home / "bin/java", java_home / "bin/javac", java_home / "lib/server/libjvm.so",
                     java_home / "lib/modules", sdk / "bin64/drrun", sdk / "lib64/release/libdynamorio.so",
                     sdk / ".durante-runtime.json"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"test input")
            path.chmod(0o700)
        output = self.root / "result"
        previous = os.sched_getaffinity(0) if hasattr(os, "sched_getaffinity") else None
        affinity_seen = []

        def execute(command, directory, timeout, prlimit):
            if previous is not None:
                affinity_seen.append(os.sched_getaffinity(0))
            if directory.name == "001-engine-only":
                (java_home / "lib/server/libjvm.so").write_bytes(b"changed JVM input")
            return self.success()

        with ExitStack() as stack:
            stack.enter_context(patch.object(tiered, "check_production_runtime"))
            stack.enter_context(patch.object(tiered, "probe_and_compile", return_value={}))
            stack.enter_context(patch.object(tiered, "run_process", side_effect=execute))
            stack.enter_context(redirect_stdout(io.StringIO()))
            code = tiered.main(["--sdk", str(sdk), "--java", str(java_home / "bin/java"),
                                "--javac", str(java_home / "bin/javac"),
                                "--output-dir", str(output), "--repeats", "3"])
        report = json.loads((output / "results.json").read_text())
        self.assertEqual(code, 1)
        self.assertEqual(report["checks_passed"], 4)
        self.assertFalse(report["inputs_unchanged"])
        self.assertFalse(report["input_provenance"]["files"]["binary:libjvm"]["unchanged"])
        self.assertTrue(report["cpu_affinity"]["restored"])
        if previous is not None:
            self.assertEqual(os.sched_getaffinity(0), previous)
            self.assertEqual(affinity_seen, [set(sorted(previous)[:4])] * 4)

    def test_actual_runtime_probe_compiles_and_matches_independent_native_sum(self):
        self.args.java = tiered.executable_path("java")
        self.args.javac = tiered.executable_path("javac")
        if not all((self.args.java, self.args.javac, self.args.prlimit)):
            self.skipTest("a complete JDK and Linux prlimit are required")
        provenance = tiered.InputProvenance({"fixture": tiered.FIXTURE})
        provenance.capture("before")
        previous = os.sched_getaffinity(0) if hasattr(os, "sched_getaffinity") else None
        owner = tiered.own_subreaper()
        try:
            if previous is not None:
                os.sched_setaffinity(0, sorted(previous)[:4])
            setup = []
            versions = tiered.probe_and_compile(self.args, provenance, time.monotonic() + 40, setup)
            native = tiered.observation_plan(self.args, [self.args.java, *tiered.JAVA_FLAGS,
                "-cp", str(self.root / "classes"), "RuntimeProbe"])[0]
            tiered.run_observations([native], self.args.prlimit, time.monotonic() + 10)
            self.assertTrue(native["passed"], native)
            self.assertEqual(native["stdout"], str(50000 * 49999 // 2) + "\n")
            self.assertIn(versions["java"]["major"], (17, 21))
            self.assertEqual(versions["gc"], "default G1")
            self.assertEqual(native["stderr"], "")
            self.assertEqual(len(setup), 3)
            provenance.capture("after")
            self.assertTrue(provenance.unchanged(), provenance.errors)
            self.assertIn("generated:RuntimeProbe.class", provenance.files)
        finally:
            cleaned = tiered.cleanup_orphans()
            restored = owner[0].prctl(36, owner[1], 0, 0, 0) == 0
            if previous is not None:
                os.sched_setaffinity(0, previous)
        self.assertTrue(cleaned)
        self.assertTrue(restored)


if __name__ == "__main__":
    unittest.main()

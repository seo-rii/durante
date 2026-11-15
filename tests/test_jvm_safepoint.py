"""Regression checks for fixed plans, symptom attribution and context evidence."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import jvm_safepoint as safepoint


FATAL = b"""# Internal Error (sharedRuntime.cpp:555), pid=6083, tid=6084
# guarantee(cb != NULL && cb->is_compiled()) failed: safepoint polling: pc must refer to an nmethod
Native frames: (J=compiled Java code, j=interpreted, Vv=VM code, C=native code)
V  [libjvm.so+0xceb2b4]  SharedRuntime::get_poll_stub(unsigned char*)+0xa4
j  java.lang.System.initPhase2(ZZ)I+0 java.base@17.0.21
Registers:
R12=0x0000000000000000, R13=0x00007f6b69883530
RIP=0x00007f6b66aeb2b4, EFLAGS=0x0000000000010246
XMM[0]=0x0
"""


class SafepointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="durante-safepoint-unit-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.args = SimpleNamespace(output_dir=self.root, repeats=2, context=False,
                                    sdk=self.root / "sdk", drrun=self.root / "sdk/bin64/drrun",
                                    java="/selected/java", javac="/selected/javac", client=self.root / "client",
                                    runner_bin=self.root / "runner", prlimit="/selected/prlimit")
        self.workload = next(w for w in safepoint.workloads.workload_cases(
            {"java": self.args.java}, self.root / "classes") if w.name == "java-processes-posix_spawn")

    @staticmethod
    def success():
        return {"root_pid": 6083, "exit_code": 0, "timeout": False,
                "cleanup_complete": True, "infrastructure_ok": True,
                "stdout": "processes:204\n", "stderr": "", "passed": True, "errors": []}

    def run_rows(self, side_effect=None):
        rows = safepoint.observation_plan(self.args, self.workload)

        def run(args, workload, mode):
            directory = args.output_dir / (workload.name + "-" + mode)
            directory.mkdir()
            return side_effect(args, workload, mode, directory) if side_effect else self.success()

        with patch.object(safepoint.workloads, "run_case", side_effect=run) as execute:
            safepoint.run_observations(self.args, self.workload, rows, time.monotonic() + 600)
        return rows, execute

    def test_fatal_snapshot_is_not_original_polling_pc(self):
        evidence = safepoint.fatal_evidence(FATAL, "hs_err_pid6083.log")
        self.assertEqual(evidence["symptom"], "safepoint_pc_lookup_guarantee")
        self.assertEqual(evidence["stage"], "jvm_bootstrap")
        self.assertEqual(evidence["fatal_register_snapshot"]["R12"], "0x0000000000000000")
        self.assertEqual(evidence["fatal_register_snapshot"]["RIP"], "0x00007f6b66aeb2b4")
        self.assertIsNone(evidence["original_polling_pc"])
        self.assertIsNone(evidence["original_signal_context"])
        self.assertEqual(evidence["pid"], 6083)
        self.assertEqual(evidence["tid"], 6084)

    def test_exact_guarantee_and_frame_are_required(self):
        for data in (FATAL.replace(b"get_poll_stub", b"unrelated"),
                     FATAL.replace(b"sharedRuntime.cpp", b"other.cpp"),
                     FATAL.replace(b"pc must refer to an nmethod", b"other guarantee"),
                     b"SIGSEGV at runtime offset 0x11b551\n"):
            with self.subTest(data=data[:80]):
                self.assertEqual(safepoint.fatal_evidence(data, "fatal.log")["symptom"], "other_jvm_fatal")
        later = safepoint.fatal_evidence(FATAL.replace(b":555", b":560"), "fatal.log")
        self.assertEqual(later["symptom"], "safepoint_pc_lookup_guarantee")
        unknown = safepoint.fatal_evidence(FATAL.replace(b"java.lang.System.initPhase2", b"RuntimeWorkload.main"), "fatal.log")
        self.assertEqual(unknown["stage"], "unestablished")

    def test_root_and_child_fatal_reports_stay_distinct(self):
        report = safepoint.fatal_evidence(FATAL, "fatal.log")
        row = {**self.success(), "exit_code": -6, "passed": False, "fatal_reports": [report]}
        self.assertEqual(safepoint.classify_failure(row), "root_safepoint_pc_guarantee")
        row["root_pid"] = 9000
        self.assertEqual(safepoint.classify_failure(row), "nonroot_safepoint_pc_guarantee")
        row["report"] = {"execution": {"root_pid": 6083}}
        self.assertEqual(safepoint.classify_failure(row), "root_safepoint_pc_guarantee")
        row["timeout"] = True
        self.assertEqual(safepoint.classify_failure(row), "timeout")
        row["timeout"] = False
        row["report"]["execution"]["state"] = "timed_out"
        self.assertEqual(safepoint.classify_failure(row), "timeout")

    def test_fixed_matrix_keeps_failure_then_later_non_reproduction(self):
        def run(args, workload, mode, directory):
            if args.output_dir.name == "001" and mode == "engine-only":
                (directory / "hs_err_pid6083.log").write_bytes(FATAL)
                return {**self.success(), "passed": False, "exit_code": -6, "errors": ["aborted"]}
            return self.success()

        rows, execute = self.run_rows(run)
        self.assertEqual(execute.call_count, 8)
        self.assertEqual([r["mode"] for r in rows], list(safepoint.workloads.MODES) * 2)
        self.assertEqual(rows[1]["failure_class"], "root_safepoint_pc_guarantee")
        self.assertFalse(rows[1]["passed"])
        self.assertTrue(rows[5]["passed"])
        self.assertEqual(len(safepoint.plan_errors(self.args, rows)), 1)
        self.assertEqual(len({r["directory"] for r in rows}), 8)

    def test_native_failure_and_cleanup_failure_leave_unrun_rows(self):
        for failure in ("native", "cleanup"):
            with self.subTest(failure=failure):
                self.args.output_dir = self.root / failure
                self.args.output_dir.mkdir()

                def run(args, workload, mode, directory):
                    if failure == "native" or mode == "engine-only":
                        return {**self.success(), "passed": False, "cleanup_complete": failure != "cleanup"}
                    return self.success()

                rows, execute = self.run_rows(run)
                attempted = 1 if failure == "native" else 2
                self.assertEqual(execute.call_count, attempted)
                self.assertTrue(all(r["state"] == "incomplete" for r in rows[attempted:]))
                self.assertTrue(safepoint.plan_errors(self.args, rows))

    def test_fatal_artifact_rejects_even_nominal_success(self):
        def run(args, workload, mode, directory):
            (directory / "hs_err_pid6083.log").write_bytes(FATAL)
            return self.success()

        rows, _ = self.run_rows(run)
        self.assertFalse(rows[0]["passed"])
        self.assertIn("JVM fatal-error report present", rows[0]["errors"])

    def test_campaign_budget_prevents_new_processes(self):
        rows = safepoint.observation_plan(self.args, self.workload)
        with patch.object(safepoint.time, "monotonic", return_value=601), \
                patch.object(safepoint.workloads, "run_case") as execute:
            safepoint.run_observations(self.args, self.workload, rows, 600)
        execute.assert_not_called()
        self.assertTrue(all(r["state"] == "incomplete" and not r["passed"] for r in rows))
        self.assertEqual(len(safepoint.plan_errors(self.args, rows)), 8)

    def test_partial_supervisor_budget_reserves_its_outer_allowance(self):
        rows = safepoint.observation_plan(self.args, self.workload)[3:4]
        with patch.object(safepoint.time, "monotonic", return_value=595), \
                patch.object(safepoint.workloads, "run_case") as execute:
            safepoint.run_observations(self.args, self.workload, rows, 600)
        execute.assert_not_called()
        self.assertEqual(rows[0]["state"], "incomplete")

    def test_short_remaining_budget_never_shortens_an_observation(self):
        for remaining, should_run in ((137.999, False), (138.0, True)):
            with self.subTest(remaining=remaining):
                rows = safepoint.observation_plan(self.args, self.workload)[1:2]
                with patch.object(safepoint.time, "monotonic", return_value=0), \
                        patch.object(safepoint.workloads, "run_case", return_value=self.success()) as execute:
                    safepoint.run_observations(self.args, self.workload, rows, remaining)
                self.assertEqual(execute.called, should_run)
                if should_run:
                    self.assertEqual(execute.call_args.args[0].timeout_ms, 120000)
                else:
                    self.assertEqual(rows[0]["state"], "incomplete")
                    self.assertIn("full observation", rows[0]["errors"][0])

    def test_missing_duplicate_or_reordered_rows_fail_plan(self):
        rows, _ = self.run_rows()
        self.assertEqual(safepoint.plan_errors(self.args, rows), [])
        for changed in (rows[:-1], rows + rows[:1], list(reversed(rows))):
            self.assertTrue(safepoint.plan_errors(self.args, changed))

    def test_fatal_inspection_is_bounded(self):
        for index in range(safepoint.MAX_FATAL_FILES + 1):
            (self.root / f"hs_err_pid{index}.log").write_bytes(FATAL)
        with self.assertRaisesRegex(ValueError, "count"):
            safepoint.inspect_fatal_reports(self.root)
        for path in self.root.glob("hs_err_pid*.log"):
            path.unlink()
        (self.root / "hs_err_pid1.log").write_bytes(b"x" * (safepoint.MAX_FATAL_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "bound"):
            safepoint.inspect_fatal_reports(self.root)

    def test_context_presence_and_truncation_cannot_invent_coverage(self):
        path = self.root / "context.jsonl"
        def write(events):
            path.write_text("".join(json.dumps({"schema": 1, "event": event}) + "\n" for event in events))
            return safepoint.inspect_context(path)
        partial = write(["configuration", "signal_stop", "inferior_exit"])
        self.assertFalse(partial["complete"])
        self.assertFalse(partial["poll_stub_argument_observed"])
        full = ["signal_stop", "hotspot_signal_context", "poll_stub_argument", "inferior_exit"]
        self.assertTrue(write(full)["complete"])
        self.assertFalse(write(full + ["truncated"])["complete"])
        self.assertFalse(write(full + ["error"])["complete"])

    def test_missing_inputs_still_publish_the_full_failed_plan(self):
        output = self.root / "missing-inputs"
        with redirect_stdout(io.StringIO()):
            code = safepoint.main(["--sdk", str(self.root / "sdk"), "--client", str(self.root / "client"),
                                  "--runner-bin", str(self.root / "runner"), "--output-dir", str(output),
                                  "--java", "/missing/java", "--javac", "/missing/javac", "--repeats", "1"])
        self.assertEqual(code, 1)
        result = json.loads((output / "results.json").read_text())
        self.assertEqual(result["planned_observations"], 4)
        self.assertEqual(result["completed_observations"], 0)
        self.assertFalse(result["passed"])
        self.assertTrue(result["failures"])
        self.assertFalse(result["runtime_qualification"])

    def test_debugger_fault_policy_actually_stops_and_passes_signals(self):
        debugger = shutil.which("gdb")
        if not debugger:
            self.skipTest("GDB unavailable; context diagnostic policy is not qualified")
        commands = []
        events = SimpleNamespace(connect=lambda _: None)
        fake = SimpleNamespace(Breakpoint=object, error=RuntimeError,
            SignalEvent=type("SignalEvent", (), {}), execute=commands.append,
            selected_thread=lambda: None, selected_inferior=lambda: SimpleNamespace(pid=0),
            events=SimpleNamespace(new_objfile=events, stop=events, exited=events))
        with patch.dict(sys.modules, {"gdb": fake}), patch.object(Path, "open", return_value=io.StringIO()):
            module = runpy.run_path(str(safepoint.ROOT / "tests/jvm_safepoint_gdb.py"))
        policy = next(c for c in commands if c.startswith("handle SIGSEGV"))
        result = subprocess.run([debugger, "--nx", "--nh", "--batch", "-ex", policy,
                                 "-ex", "info signals SIGSEGV", "-ex", "info signals SIGBUS",
                                 "-ex", "info signals SIGABRT"], text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("SIGSEGV", "SIGBUS", "SIGABRT"):
            self.assertRegex(result.stdout, name + r"\s+Yes\s+Yes\s+Yes")
        transcript = self.root / "debugger-transcript.log"
        command = safepoint.debugger_command(debugger, [])
        # This configuration-only probe starts no inferior to supply an exit code.
        command = [arg.replace("debugger-transcript.log", str(transcript))
                   for arg in command[:command.index("-x")] if arg != "--return-child-result"]
        command += ["-ex", r"echo context-transcript-receipt\n"]
        logged = subprocess.run(command, text=True, capture_output=True, timeout=5)
        self.assertEqual(logged.returncode, 0, logged.stderr)
        self.assertNotIn("context-transcript-receipt", logged.stdout)
        self.assertIn("context-transcript-receipt", transcript.read_text())

        # Exhaust the bounded event stream using the actual debugger recorder;
        # termination and coverage metadata must survive without lifting its cap.
        output = io.StringIO()
        record = module["record"]
        record.__globals__.update(stream=output, count=0, truncated=False, MAX_EVENTS=1)
        record("configuration")
        record("signal_stop")
        record("inferior_exit", terminal=True, exit_code=0)
        record("capture_summary", terminal=True, installed_probe_kinds=[])
        path = self.root / "capped-context.jsonl"
        path.write_text(output.getvalue())
        evidence = safepoint.inspect_context(path)
        self.assertEqual(evidence["events"], 4)
        self.assertTrue(evidence["inferior_exit_observed"])
        self.assertFalse(evidence["complete"])
        self.assertEqual(evidence["errors"][0]["event"], "truncated")

    def test_debugger_signal_stop_output_stays_in_bounded_transcript(self):
        debugger, prlimit = shutil.which("gdb"), shutil.which("prlimit")
        if not debugger or not prlimit:
            self.skipTest("GDB/prlimit unavailable; live output routing is not qualified")
        directory = self.root / "signal-control"
        directory.mkdir()
        target = ("import os,signal\n"
                  "signal.signal(signal.SIGSEGV,lambda *_:None)\n"
                  "for _ in range(8): os.kill(os.getpid(),signal.SIGSEGV)\n"
                  "print('signal-control:8')\n")
        command = safepoint.debugger_command(debugger, [sys.executable, "-c", target])
        result = safepoint.run_process(command, directory, 10000, prlimit, cpu_seconds=5)
        self.assertTrue(result["infrastructure_ok"], result)
        self.assertTrue(result["cleanup_complete"], result)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(result["stdout"], "signal-control:8\n")
        transcript = safepoint.bounded_bytes(directory / "debugger-transcript.log", safepoint.MAX_FATAL_BYTES)
        self.assertEqual(transcript.count(b"received signal SIGSEGV"), 8)
        self.assertIn(b"exited normally", transcript)
        evidence = safepoint.inspect_context(directory / "jvm-context.jsonl")
        self.assertTrue(evidence["signal_stop_observed"])
        self.assertTrue(evidence["inferior_exit_observed"])
        self.assertFalse(evidence["complete"])
        self.assertEqual(evidence["errors"], [])


if __name__ == "__main__":
    unittest.main()

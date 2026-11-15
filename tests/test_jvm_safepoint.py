"""Regression checks for fixed plans, symptom attribution and context evidence."""
from contextlib import redirect_stdout
import copy
import io
import json
import os
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


def debugger_module():
    commands = []
    events = SimpleNamespace(connect=lambda _: None)
    fake = SimpleNamespace(Breakpoint=object, FinishBreakpoint=object, error=RuntimeError,
        SignalEvent=type("SignalEvent", (), {}), execute=commands.append,
        selected_thread=lambda: None, selected_inferior=lambda: SimpleNamespace(pid=0),
        events=SimpleNamespace(new_objfile=events, stop=events, exited=events))
    with patch.dict(sys.modules, {"gdb": fake}), patch.object(Path, "open", return_value=io.StringIO()):
        module = runpy.run_path(str(safepoint.ROOT / "tests/jvm_safepoint_gdb.py"))
    return module, commands, fake


class SafepointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="durante-safepoint-unit-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.addCleanup(self.preserve_debugger_failure)
        self.args = SimpleNamespace(output_dir=self.root, repeats=2, context=False,
                                    sdk=self.root / "sdk", drrun=self.root / "sdk/bin64/drrun",
                                    java="/selected/java", javac="/selected/javac", client=self.root / "client",
                                    runner_bin=self.root / "runner", prlimit="/selected/prlimit")
        self.workload = next(w for w in safepoint.workloads.workload_cases(
            {"java": self.args.java}, self.root / "classes") if w.name == "java-processes-posix_spawn")

    def preserve_debugger_failure(self):
        result = getattr(self._outcome, "result", None)
        failures = getattr(result, "failures", []) + getattr(result, "errors", [])
        if not any(test is self or getattr(test, "test_case", None) is self for test, _ in failures):
            return
        # These are owned fixture outputs, retained before TemporaryDirectory
        # cleanup so the existing .results CI artifact includes startup errors.
        names = {"debugger-transcript.log", "jvm-context.jsonl", "jvm-context-config.json",
                 "stdout.txt", "stderr.txt", "fault.c", "fault"}
        files = [path for path in self.root.rglob("*") if path.is_file() and path.name in names]
        if not any(path.name == "debugger-transcript.log" for path in files):
            return
        base = safepoint.ROOT / ".results/jvm-context-unit"
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = Path(tempfile.mkdtemp(prefix=self._testMethodName + "-", dir=base))
        for path in files:
            target = destination / path.relative_to(self.root)
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.write_bytes(safepoint.bounded_bytes(path, safepoint.MAX_FATAL_BYTES))

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

    def test_native_translation_pairs_preserve_failed_or_zero_pc_results(self):
        path = self.root / "native-context.jsonl"
        identity = {"schema": 2, "inferior_pid": 70, "thread_ptid": [70, 71, 0],
                    "translation_id": 1, "ucontext_address": "0x1230"}
        context = {"rip": "0x4000", "rsp": "0x5000", "err": "0x2", "trapno": "0xe", "cr2": "0x6000"}
        events = [
            {**identity, "event": "translation_enter", "pre_context": context},
            {**identity, "event": "translation_return", "result": False, "post_context": {**context, "rip": "0x0"}},
            {"schema": 2, "event": "inferior_exit"},
            {"schema": 2, "event": "capture_summary", "translations_entered": 1,
             "translations_returned": 1, "pending_returns": 0,
             "installed_probe_kinds": ["native_translation"], "missing_probe_kinds": [],
             "event_limit_reached": False, "raw_signal_stops_seen": 2000,
             "raw_signal_stops_sampled_out": 1984, "critical_translations": 1,
             "first_critical_translation": {**identity, "result": False,
                                             "pre_context": context,
                                             "post_context": {**context, "rip": "0x0"}}}]

        def inspect(rows):
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            return safepoint.inspect_context(path)

        evidence = inspect(events)
        self.assertTrue(evidence["complete"])
        self.assertEqual(evidence["translation_failures"], [1])
        self.assertEqual(evidence["zero_post_pc"], [1])
        self.assertFalse(evidence["hotspot_context_observed"])
        self.assertFalse(evidence["poll_stub_argument_observed"])
        self.assertEqual(evidence["raw_signal_stops_sampled_out"], 1984)
        for changed in (events[:1] + events[2:], events + [{"schema": 2, "event": "truncated"}]):
            self.assertFalse(inspect(changed)["complete"])
        for mutation in ({"translation_id": 2}, {"thread_ptid": [70, 72, 0]},
                         {"inferior_pid": 75}, {"ucontext_address": "0x4560"}, {"result": 1},
                         {"post_context": {}}, {"post_context": {**context, "rip": "0x10000000000000000"}}):
            changed = copy.deepcopy(events)
            changed[1].update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                inspect(changed)
        for key in context:
            changed = copy.deepcopy(events)
            del changed[0]["pre_context"][key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                inspect(changed)
        for invalid in (-1, True, "1", None):
            changed = copy.deepcopy(events)
            changed[-1]["translations_entered"] = invalid
            with self.subTest(summary_count=invalid), self.assertRaises(ValueError):
                inspect(changed)

    def test_native_runtime_probe_observes_exact_fault_pc_and_boolean_return(self):
        requested = os.environ.get("DURANTE_TEST_CONTEXT_SDK")
        sdk = Path(requested) if requested else safepoint.ROOT / "toolkit/sdk/DynamoRIO-Linux-11.3.0-1"
        debugger, compiler, prlimit, nm = (shutil.which(name) for name in ("gdb", "gcc", "prlimit", "nm"))
        required = (sdk / "bin64/drrun", sdk / "lib64/release/libdynamorio.so.debug")
        available = all((debugger, compiler, prlimit, nm)) and all(path.is_file() for path in required)
        if not available and not requested:
            self.skipTest("prepared SDK/GDB unavailable; native translation probe requires its live control")
        self.assertTrue(available, "explicit context-control SDK and required tools must be available")
        source, executable = self.root / "fault.c", self.root / "fault"
        source.write_text(r'''#define _GNU_SOURCE
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/mman.h>
#include <ucontext.h>
#include <unistd.h>
extern char durante_context_fault[];
static void *page;
static volatile sig_atomic_t seen, correct;
static void handled(int signal, siginfo_t *info, void *pointer) {
    ucontext_t *context = pointer;
    correct = signal == SIGSEGV && info->si_addr == page &&
        (uintptr_t) context->uc_mcontext.gregs[REG_RIP] == (uintptr_t) durante_context_fault;
    if (mprotect(page, 4096, PROT_READ | PROT_WRITE)) _exit(90);
    ++seen;
}
int main(void) {
    page = mmap(0, 4096, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (page == MAP_FAILED) return 2;
    struct sigaction action = {0};
    action.sa_sigaction = handled; action.sa_flags = SA_SIGINFO;
    if (sigaction(SIGSEGV, &action, 0)) return 3;
    __asm__ volatile(".global durante_context_fault\ndurante_context_fault:\nmovb $9,(%0)"
                     : : "r"(page) : "memory");
    if (seen != 1 || !correct || *(unsigned char *)page != 9) return 4;
    puts("native-translation-control:1");
    return 0;
}
''')
        compiled = subprocess.run([compiler, "-O0", "-g", "-no-pie", str(source), "-o", str(executable)],
                                  capture_output=True, timeout=10)
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        symbols = subprocess.run([nm, "-n", str(executable)], text=True, capture_output=True, timeout=5)
        self.assertEqual(symbols.returncode, 0, symbols.stderr)
        expected_pc = next(int(line.split()[0], 16) for line in symbols.stdout.splitlines()
                           if line.endswith(" durante_context_fault"))
        native_dir = self.root / "native-fault"
        native_dir.mkdir()
        native = safepoint.run_process([str(executable)], native_dir, 5000, prlimit, cpu_seconds=5)
        self.assertEqual(native["exit_code"], 0, native)
        self.assertEqual(native["stdout"], "native-translation-control:1\n")
        self.assertTrue(native["cleanup_complete"])
        args = SimpleNamespace(sdk=sdk.resolve(), drrun=sdk.resolve() / "bin64/drrun", gdb=debugger, prlimit=prlimit)
        directory = self.root / "engine-fault"
        observed = safepoint.run_context(args, SimpleNamespace(command=[str(executable)]), directory, 15000)
        self.assertTrue(observed["passed"], observed)
        self.assertEqual(observed["stdout"], native["stdout"])
        self.assertTrue(observed["cleanup_complete"])
        rows = [json.loads(line) for line in (directory / "jvm-context.jsonl").read_text().splitlines()]
        entered = {row["translation_id"]: row for row in rows if row["event"] == "translation_enter"}
        matching = [row for row in rows if row["event"] == "translation_return"
                    and int(row["post_context"]["rip"], 16) == expected_pc]
        self.assertEqual(len(matching), 1, rows)
        self.assertIs(matching[0]["result"], True)
        self.assertNotEqual(entered[matching[0]["translation_id"]]["pre_context"]["rip"], hex(expected_pc))
        self.assertTrue(any(row["event"] == "breakpoint_installed" and row.get("build_id") for row in rows))

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
        module, commands, _ = debugger_module()
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
        record("capture_summary", terminal=True, installed_probe_kinds=[],
               translations_entered=0, translations_returned=0, pending_returns=0,
               raw_signal_stops_seen=0, raw_signal_stops_sampled_out=0, critical_translations=0)
        path = self.root / "capped-context.jsonl"
        path.write_text(output.getvalue())
        evidence = safepoint.inspect_context(path)
        self.assertEqual(evidence["events"], 4)
        self.assertTrue(evidence["inferior_exit_observed"])
        self.assertFalse(evidence["complete"])
        self.assertEqual(evidence["errors"][0]["event"], "truncated")

    def test_return_probe_retains_critical_context_after_event_cap_and_reports_missing_returns(self):
        module, _, fake = debugger_module()
        fake.selected_thread = lambda: SimpleNamespace(ptid=(70, 71, 0))
        fake.selected_inferior = lambda: SimpleNamespace(pid=70)
        state = module["record"].__globals__
        output = io.StringIO()
        before = {"rip": "0x4000", "rsp": "0x5000", "err": "0x2", "trapno": "0xe", "cr2": "0x6000"}
        state.update(stream=output, count=1, MAX_EVENTS=1, truncated=False,
                     context=lambda _: {**before, "rip": "0x0"})
        probe_type = module["TranslationReturn"]
        probe = probe_type.__new__(probe_type)
        probe.identifier, probe.pointer, probe.before, probe.return_value = 7, 0x1230, before, 0
        state["pending"][7] = probe
        self.assertFalse(probe.stop())
        self.assertNotIn(7, state["pending"])
        self.assertEqual(state["critical_translations"], 1)
        self.assertIs(state["critical_sample"]["result"], False)
        self.assertEqual(state["critical_sample"]["post_context"]["rip"], "0x0")
        self.assertEqual(json.loads(output.getvalue())["event"], "truncated")

        for return_value in (None, 2):
            output = io.StringIO()
            state.update(stream=output, count=0, MAX_EVENTS=256, truncated=False)
            probe.return_value = return_value
            state["pending"][7] = probe
            self.assertFalse(probe.stop())
            self.assertEqual(json.loads(output.getvalue())["event"], "error")
            self.assertNotIn(7, state["pending"])
        output = io.StringIO()
        state.update(stream=output, count=0)
        state["pending"][7] = probe
        probe.out_of_scope()
        self.assertNotIn(7, state["pending"])
        self.assertIn("without an observed return", json.loads(output.getvalue())["detail"])

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
        if not result["infrastructure_ok"] or result["exit_code"] != 0:
            result["debugger_transcript"] = safepoint.debugger_transcript_evidence(directory, include_excerpt=True)
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

    def test_debugger_library_environment_is_separate_and_target_value_is_exact(self):
        debugger, prlimit = shutil.which("gdb"), shutil.which("prlimit")
        if not debugger or not prlimit:
            self.skipTest("GDB/prlimit unavailable; library environment is not qualified")
        target = ["/bin/sh", "-c", 'if [ "${LD_LIBRARY_PATH+x}" = x ]; then printf "set:%s" "$LD_LIBRARY_PATH"; else printf unset; fi']
        for index, value in enumerate((None, "", 'directory with spaces=one:"quoted":two')):
            with self.subTest(value=value), patch.dict(os.environ):
                if value is None:
                    os.environ.pop("LD_LIBRARY_PATH", None)
                else:
                    os.environ["LD_LIBRARY_PATH"] = value
                directory = self.root / ("library-environment-" + str(index))
                directory.mkdir()
                command = safepoint.debugger_command(debugger, target)
                insertion = command.index("-x")
                command[insertion:insertion] = ["-ex", "python import os,math; assert 'LD_LIBRARY_PATH' not in os.environ; print('debugger-library-environment:clean')"]
                result = safepoint.run_process(command, directory, 10000, prlimit, cpu_seconds=5)
                result["debugger_transcript"] = safepoint.debugger_transcript_evidence(directory, include_excerpt=True)
                self.assertEqual(result["exit_code"], 0, result)
                self.assertIn("debugger-library-environment:clean\n", result["debugger_transcript"]["excerpt"], result)
                self.assertTrue(result["cleanup_complete"], result)
                self.assertEqual(result["stdout"], "unset" if value is None else "set:" + value)
                self.assertEqual(os.environ.get("LD_LIBRARY_PATH"), value)

    def test_startup_error_retains_bounded_transcript_text(self):
        directory = self.root / "startup-error"
        transcript = "Python Exception <class 'ModuleNotFoundError'>: No module named 'math'\nError occurred in Python: No module named 'math'\n"
        args = SimpleNamespace(sdk=self.root, drrun=self.root / "drrun", gdb="/selected/gdb", prlimit="/selected/prlimit")

        def failed(*_args, **_kwargs):
            (directory / "debugger-transcript.log").write_text(transcript)
            return {**self.success(), "exit_code": 1, "stdout": ""}

        with patch.object(safepoint, "run_process", side_effect=failed):
            row = safepoint.run_context(args, self.workload, directory, 1000)
        self.assertFalse(row["passed"])
        self.assertEqual(row["debugger_transcript"]["excerpt"], transcript)
        self.assertEqual(row["debugger_transcript"]["sha256"], "0b305aa30831a95ae603c71aa4aec6c56e489c4ee0766bd00db5718bcc5fcaa4")
        (directory / "debugger-transcript.log").write_bytes(b"head\n" + b"x" * 20000 + b"\ntail")
        evidence = safepoint.debugger_transcript_evidence(directory, include_excerpt=True)
        self.assertTrue(evidence["excerpt_truncated"])
        self.assertLess(len(evidence["excerpt"].encode()), 8300)
        self.assertTrue(evidence["excerpt"].startswith("head\n"))
        self.assertTrue(evidence["excerpt"].endswith("\ntail"))


if __name__ == "__main__":
    unittest.main()

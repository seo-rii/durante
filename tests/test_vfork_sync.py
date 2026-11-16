"""Keep the fixed debug startup notice separate from every vfork failure gate."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import runtime_workloads as workloads
import vfork_sync


NOTICE = "<Unknown processor cache size>\n"
DIAGNOSTIC_ERROR = "unexpected application or runtime diagnostic"


class VforkDiagnosticTests(unittest.TestCase):
    def observe(self, *, profile="debug", mode="engine-only", call="clone", action="exit",
                stderr=NOTICE, observation=None, records_change=None, measurement_change=None,
                execution_change=None, collector_stderr=""):
        # Only external execution and the Rust collector subprocess are mocked.
        # run_case still checks the real protocol records, topology, supervisor
        # envelope, exact fixture output, infrastructure and diagnostic gates.
        with tempfile.TemporaryDirectory(prefix="durante-vfork-diagnostic-unit-") as temporary:
            root = Path(temporary)
            args = SimpleNamespace(output_dir=root, timeout_ms=1000, prlimit="/prlimit",
                                   runner_bin=Path("/durante"), drrun=Path("/drrun"),
                                   client=Path("/libdurante.so"))
            workload = vfork_sync.fixture(root / "fixture", call, action, mode == "native", "case")
            measurements = []

            def execute(command, directory, timeout_ms, prlimit, **controls):
                if mode in ("client-rep-off", "supervisor"):
                    vectors = json.loads((workloads.ROOT / "tests/protocol_vectors.json").read_text())["cases"]
                    selected = "vfork-exec" if action == "exec" else "fork"
                    files = copy.deepcopy(next(case["files"] for case in vectors if case["name"] == selected))
                    for rows in files.values():
                        for row in rows:
                            if row["event"] == "start":
                                row["rep_bytes_enabled"] = mode == "supervisor"
                            if row["pid"] == 1 and row["event"] == "end":
                                row["threads_started"] = row["threads_finished"] = 3
                    if records_change:
                        records_change(files)
                    records = directory / "records"
                    records.mkdir(exist_ok=True)
                    for name, rows in files.items():
                        (records / name).write_text("".join(json.dumps(row) + "\n" for row in rows))
                    measurement = workloads.collector.collect(records, 1)
                    if measurement_change:
                        measurement_change(measurement)
                    measurements.append(measurement)
                    (directory / "collector-stderr.txt").write_text(collector_stderr)
                    if mode == "supervisor":
                        execution = {"state": "complete", "exit_code": 0, "root_pid": 1,
                                     "cleanup_complete": True, "records_cleanup_complete": True}
                        if execution_change:
                            execution.update(execution_change)
                        envelope = {"authority": "diagnostic_only", "integrity_verified": False,
                                    "measurement_contract": workloads.collector.measurement_contract(),
                                    "security": {"counter_integrity": "application_mutable",
                                                 "isolation": "external_required"},
                                    "execution": execution, "measurement": measurement}
                        (directory / "report.json").write_text(json.dumps(envelope))
                row = {"root_pid": 1, "exit_code": 0, "stdout": vfork_sync.OUTPUT, "stderr": stderr,
                       "timeout": False, "cleanup_complete": True, "infrastructure_ok": True, "errors": []}
                row.update(copy.deepcopy(observation or {}))
                (directory / "stdout.txt").write_text(row["stdout"])
                (directory / "stderr.txt").write_text(row["stderr"])
                return row

            with patch.object(workloads, "run_process", side_effect=execute), \
                    patch.object(workloads, "collect_records", side_effect=lambda *args: measurements[-1]):
                row = vfork_sync.run_case(args, workload, mode)
            row.update(profile=profile, call=call, action=action, repeat=0)
            original = copy.deepcopy(row)
            evidence = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            vfork_sync.classify_diagnostic(row)
            # Classification may only change these three fields, never stdout,
            # stderr, measurements, supervisor reports or raw record files.
            mutable = {"errors", "passed", "diagnostic_classification"}
            self.assertEqual({key: value for key, value in row.items() if key not in mutable},
                             {key: value for key, value in original.items() if key not in mutable})
            self.assertEqual({path: path.read_bytes() for path in evidence}, evidence)
            return original, row

    def test_fixed_debug_cases_classify_only_the_startup_notice(self):
        for call in vfork_sync.CALLS:
            for action in vfork_sync.ACTIONS:
                for mode in vfork_sync.MODES:
                    for count in ((1, 2) if action == "exec" else (1,)):
                        with self.subTest(call=call, action=action, mode=mode, count=count):
                            original, row = self.observe(call=call, action=action, mode=mode, stderr=NOTICE * count)
                            self.assertFalse(original["passed"])  # The shared stderr policy stays strict.
                            self.assertEqual(original["errors"], [DIAGNOSTIC_ERROR])
                            self.assertTrue(row["passed"], row["errors"])
                            self.assertEqual(row["errors"], [])
                            expected = "cpu_cache_size_unknown" if count == 1 else "cpu_cache_size_unknown_after_exec"
                            self.assertEqual(row["diagnostic_classification"], expected)

    def test_empty_stderr_uses_the_unchanged_shared_result(self):
        for mode in ("native", *vfork_sync.MODES):
            with self.subTest(mode=mode):
                original, row = self.observe(mode=mode, stderr="")
                self.assertTrue(original["passed"], original["errors"])
                self.assertTrue(row["passed"], row["errors"])
                self.assertEqual(row["diagnostic_classification"], "none")

    def test_notice_is_unexpected_outside_the_fixed_debug_matrix(self):
        for change in ({"profile": "release"}, {"profile": None}, {"profile": "other"},
                       {"mode": "native"}, {"mode": "unknown"}, {"call": "vfork"},
                       {"call": None}, {"action": "unknown"}, {"action": None}):
            with self.subTest(change=change):
                original, row = self.observe(**change)
                self.assertFalse(row["passed"])
                self.assertEqual(row["errors"], original["errors"])
                self.assertEqual(row["diagnostic_classification"], "unexpected")

    def test_other_diagnostics_and_nonexact_notice_bytes_still_fail(self):
        for action, stderr in (
                ("exit", NOTICE * 2), ("failed-exec", NOTICE * 2), ("failed-clone", NOTICE * 2),
                ("exec", NOTICE * 3), ("exit", NOTICE.rstrip("\n")),
                ("exit", NOTICE.replace("\n", "\r\n")), ("exit", NOTICE + " "),
                ("exit", NOTICE.lower()), ("exit", "assertion failed\n" + NOTICE),
                ("exec", NOTICE * 2 + "Option parsing error\n")):
            for mode in vfork_sync.MODES:
                with self.subTest(action=action, stderr=stderr, mode=mode):
                    original, row = self.observe(action=action, stderr=stderr, mode=mode)
                    self.assertFalse(row["passed"])
                    self.assertEqual(row["errors"], original["errors"])
                    self.assertEqual(row["diagnostic_classification"], "unexpected")

    def test_notice_does_not_hide_execution_or_infrastructure_failure(self):
        changes = (
            ({"stdout": "incorrect result\n"}, "exact fixture output or exit oracle failed"),
            ({"exit_code": 7}, "exact fixture output or exit oracle failed"),
            ({"timeout": True, "infrastructure_ok": False, "errors": ["outer wall deadline expired"]},
             "outer wall deadline expired"),
            ({"cleanup_complete": False, "infrastructure_ok": False, "errors": ["descendant cleanup failed"]},
             "descendant cleanup failed"),
            ({"infrastructure_ok": False}, None),
        )
        for mode in vfork_sync.MODES:
            for change, expected in changes:
                with self.subTest(mode=mode, change=change):
                    _, row = self.observe(mode=mode, observation=change)
                    self.assertFalse(row["passed"])
                    self.assertNotIn(DIAGNOSTIC_ERROR, row["errors"])
                    if expected:
                        self.assertIn(expected, row["errors"])

    def test_only_one_shared_diagnostic_error_can_be_removed(self):
        original, row = self.observe(observation={"errors": [DIAGNOSTIC_ERROR]})
        self.assertEqual(original["errors"], [DIAGNOSTIC_ERROR, DIAGNOSTIC_ERROR])
        self.assertEqual(row["errors"], [DIAGNOSTIC_ERROR])
        self.assertFalse(row["passed"])

    def test_notice_does_not_hide_accounting_failures(self):
        changes = (
            ({"records_change": lambda files: files.pop("process-2.jsonl")},
             "complete process-tree measurement unavailable"),
            ({"records_change": lambda files: files["process-1.jsonl"][-1].update(
                threads_started=1, threads_finished=1)}, "root image did not account for the required worker threads"),
            ({"records_change": lambda files: files["process-2.jsonl"][-1].update(app_exit_code=7)},
             "child launch, image distribution or normal exit differs from the fixed topology"),
            ({"measurement_change": lambda measurement: measurement.update(instructions=999)},
             "Rust report differs from independently collected raw records"),
            ({"measurement_change": lambda measurement: measurement.update(
                rep_bytes_enabled=not measurement["rep_bytes_enabled"])},
             "REP mode differs from the selected execution"),
        )
        for mode in ("client-rep-off", "supervisor"):
            for change, expected in changes:
                with self.subTest(mode=mode, expected=expected):
                    _, row = self.observe(mode=mode, action="exec", stderr=NOTICE * 2, **change)
                    self.assertFalse(row["passed"])
                    self.assertIn(expected, row["errors"])
                    self.assertNotIn(DIAGNOSTIC_ERROR, row["errors"])

    def test_notice_does_not_hide_supervisor_target_or_cleanup_failure(self):
        for change in ({"state": "timed_out"}, {"exit_code": 7}, {"cleanup_complete": False},
                       {"records_cleanup_complete": False}):
            with self.subTest(change=change):
                _, row = self.observe(mode="supervisor", execution_change=change)
                self.assertFalse(row["passed"])
                self.assertIn("supervisor target execution or cleanup incomplete", row["errors"])

    def test_collector_diagnostics_remain_failures(self):
        _, row = self.observe(mode="client-rep-off", collector_stderr="collector warning\n")
        self.assertFalse(row["passed"])
        self.assertIn("unexpected collector diagnostic", row["errors"])


if __name__ == "__main__":
    unittest.main()

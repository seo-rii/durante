"""Strict isolation gates without requiring a Docker daemon or runtime build."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import isolated_runtimes as isolated


class IsolatedRuntimeTests(unittest.TestCase):
    def fixture(self):
        cases = json.loads((isolated.ROOT / "tests/protocol_vectors.json").read_text())["cases"]
        with tempfile.TemporaryDirectory() as directory:
            records = Path(directory)
            for name, rows in cases[0]["files"].items():
                (records / name).write_text("".join(json.dumps(row) + "\n" for row in rows))
            measurement = isolated.collector.collect(records, cases[0]["root_pid"])
        measurement["rep_bytes_enabled"] = True
        contract = isolated.collector.measurement_contract()
        inner = {"authority": "diagnostic_only", "integrity_verified": False, "measurement_contract": contract,
                 "security": {"counter_integrity": "application_mutable", "isolation": "external_required"},
                 "execution": {"state": "complete", "exit_code": 0, "root_pid": cases[0]["root_pid"],
                               "cleanup_complete": True, "records_cleanup_complete": True},
                 "measurement": measurement}
        report = {"authority": "diagnostic_only", "integrity_verified": False, "measurement_contract": contract,
                  "security": {"counter_integrity": "application_mutable", "isolation": "bubblewrap-0.12-durante-pid1-v1"},
                  "execution": {"state": "complete", "wrapper_exit_code": 0, "cleanup_complete": True,
                                "scratch_cleanup_complete": True}, "untrusted_inner_report": inner,
                  "output": {"mode": "captured", "published": True, "complete": True, "truncated": False,
                             "limit_bytes": isolated.CAPTURE_LIMIT, "stdout_captured_bytes": 3, "stderr_captured_bytes": 0}}
        return report, isolated.Workload("fixture", "cpython", [], "ok\n", 1, 1), (b"ok\n", b"")

    def test_success_needs_both_execution_layers_exact_capture_and_cleanup(self):
        report, workload, captured = self.fixture()
        self.assertEqual(isolated.isolated_errors(report, workload, captured), [])
        mutations = (
            lambda value: value["execution"].update(state="timed_out"),
            lambda value: value["execution"].update(scratch_cleanup_complete=False),
            lambda value: value["output"].update(complete=False),
            lambda value: value["output"].update(published=False),
            lambda value: value["untrusted_inner_report"]["execution"].update(exit_code=7),
            lambda value: value["untrusted_inner_report"]["measurement"].update(valid=False),
            lambda value: value["untrusted_inner_report"]["measurement"].update(instructions=True),
            lambda value: value["untrusted_inner_report"]["measurement"].update(process_count=True),
            lambda value: value["measurement_contract"].update(trusted_verdict_eligible=True),
        )
        for mutate in mutations:
            value = copy.deepcopy(report)
            mutate(value)
            self.assertTrue(isolated.isolated_errors(value, workload, captured))
        self.assertTrue(isolated.isolated_errors(report, workload, (b"no\n", b"")))

    def test_missing_native_or_isolated_runtime_cannot_count_as_coverage(self):
        _, workload, _ = self.fixture()
        rows = [{"workload": "fixture", "mode": mode, "passed": True} for mode in ("native", "isolate")]
        self.assertEqual(isolated.matrix_errors([workload], rows), [])
        self.assertTrue(isolated.matrix_errors([workload], rows[:1]))
        self.assertTrue(isolated.matrix_errors([workload], rows + rows))
        rows[1]["passed"] = False
        self.assertTrue(isolated.matrix_errors([workload], rows))

    def test_relocated_map_check_requires_all_three_libraries_at_physical_version(self):
        paths = ["/relocated version/lib/libdurante.so", "/relocated version/sdk/ext/lib64/release/libdrmgr.so",
                 "/relocated version/sdk/lib64/release/libdynamorio.so"]
        def maps(names):
            return "".join("1000-2000 r-xp 0000 00:00 1 " + name + "\n" for name in names).encode()
        self.assertEqual(len(isolated.relocated_libraries(maps(paths))), 3)
        for changed in (paths[:-1], paths[:-1] + ["/sdk/lib64/release/libdynamorio.so"]):
            with self.assertRaises(ValueError):
                isolated.relocated_libraries(maps(changed))

    def test_inner_success_never_substitutes_for_external_owner_cleanup(self):
        observation = {"passed": True, "cleanup_complete": True}
        teardown = {key: True for key in (
            "cleanup_complete", "container_removed", "live_before_owner_stop", "owner_kill_succeeded")}
        self.assertEqual(isolated.owner_errors(observation, teardown), [])
        for key in teardown:
            self.assertTrue(isolated.owner_errors(observation, teardown | {key: False}))
        self.assertTrue(isolated.owner_errors(observation | {"passed": False}, teardown))
        self.assertTrue(isolated.owner_errors(observation | {"cleanup_complete": False}, teardown))

    def test_fixture_pipe_bound_and_deadline_are_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            normal = isolated.pipe_run([sys.executable, "-c", "print('ok')"], root / "normal", 1000)
            self.assertEqual(normal["stdout"], "ok\n")
            self.assertEqual(normal["errors"], [])
            large = isolated.pipe_run([sys.executable, "-c", "print('x'*100000)"], root / "large", 1000)
            self.assertTrue(large["errors"])
            self.assertLessEqual((root / "large/stdout.txt").stat().st_size, isolated.CAPTURE_LIMIT)
            slow = isolated.pipe_run([sys.executable, "-c", "import time;time.sleep(10)"], root / "slow", 100)
            self.assertTrue(slow["errors"])
            self.assertTrue(slow["cleanup_complete"])

    def test_staged_driver_imports_without_repository_or_sdk_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(isolated.subprocess, "run"):
                driver = isolated.prepare_driver(SimpleNamespace(work_dir=Path(directory)))
            self.assertFalse((driver / "toolkit/sdk").exists())
            self.assertFalse((driver / "build").exists())
            result = subprocess.run([sys.executable, "-I", "-S", "-c",
                "import sys; sys.path.insert(0, 'tests'); import isolated_runtimes"],
                cwd=driver, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

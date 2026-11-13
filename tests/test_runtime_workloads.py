"""Exercise fixed outputs and failure gates without a DynamoRIO build."""
from contextlib import ExitStack, redirect_stdout
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import runtime_workloads as workloads


class RuntimeWorkloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="durante-workload-unit-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def records(self):
        case = json.loads((workloads.ROOT / "tests/protocol_vectors.json").read_text())["cases"][0]
        records = self.root / "records"
        records.mkdir()
        for name, rows in case["files"].items():
            (records / name).write_text("".join(json.dumps(row) + "\n" for row in rows))
        result = workloads.collector.collect(records, case["root_pid"])
        self.assertTrue(result["valid"])
        return records, result

    def test_cpython_workloads_execute_with_independent_exact_outputs(self):
        for name, expected in workloads.OUTPUTS.items():
            with self.subTest(workload=name):
                result = subprocess.run([sys.executable, "-I", "-S", str(workloads.FIXTURES / "workload.py"), name],
                                        capture_output=True, text=True, timeout=20, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)
                self.assertEqual(result.stderr, "")

    def test_exact_launch_topology_includes_both_jspawnhelper_images(self):
        cases = workloads.workload_cases({"java": "/selected/java", "node": "/selected/node",
                                           "cpython": "/selected/python"}, self.root)
        by_name = {case.name: case for case in cases}
        self.assertEqual((by_name["java-processes-posix_spawn"].processes,
                          by_name["java-processes-posix_spawn"].images), (3, 7))
        for name in ("java-processes-fork", "node-processes", "cpython-processes"):
            self.assertEqual((by_name[name].processes, by_name[name].images), (3, 5))
        self.assertIn("-Djdk.lang.Process.launchMechanism=POSIX_SPAWN",
                      by_name["java-processes-posix_spawn"].command)
        self.assertIn("-Djdk.lang.Process.launchMechanism=FORK", by_name["java-processes-fork"].command)

    def test_accepted_measurement_must_equal_the_real_reference_collector(self):
        records, result = self.records()
        case = workloads.Workload("fixture", "cpython", [], "", 1, 1)
        self.assertEqual(workloads.measurement_errors(result, records, 1, case, False), [])
        for field, value in (("instructions", 101), ("authority", "trusted"),
                             ("integrity_verified", True), ("process_count", 2),
                             ("process_count", True), ("image_count", 1.0),
                             ("image_count", 3), ("rep_bytes_enabled", True)):
            with self.subTest(field=field):
                changed = copy.deepcopy(result)
                changed[field] = value
                self.assertTrue(workloads.measurement_errors(changed, records, 1, case, False))

    def test_valid_but_missing_children_is_a_failure(self):
        records, result = self.records()
        case = workloads.Workload("two-children", "java", [], "", 3, 7)
        errors = workloads.measurement_errors(result, records, 1, case, False)
        self.assertIn("process/image topology differs from the fixed launch oracle", errors)

    def test_invalid_measurement_cannot_be_hidden_by_plausible_totals(self):
        records, result = self.records()
        result.update(valid=False, complete=False)
        errors = workloads.measurement_errors(result, records, 1,
                                               workloads.Workload("fixture", "java", [], "", 1, 1), False)
        self.assertIn("invalid measurement exposed partial totals", errors)

    def test_thread_fixture_rejects_otherwise_valid_single_thread_records(self):
        records, result = self.records()
        case = workloads.Workload("cpython-threads", "cpython", [], "", 1, 1, 3)
        errors = workloads.measurement_errors(result, records, 1, case, False)
        self.assertIn("root image did not account for the required worker threads", errors)

    def test_process_topology_checks_each_child_and_its_parent_image(self):
        case = workloads.Workload("java-processes-posix_spawn", "java", [], "", 3, 7)
        report = {"root_pid": 10,
                  "processes": [{"pid": 10, "parent_pid": 0, "parent_segment": "", "images": 1, "exit_code": 0},
                                {"pid": 11, "parent_pid": 10, "parent_segment": "root", "images": 3, "exit_code": 0},
                                {"pid": 12, "parent_pid": 10, "parent_segment": "root", "images": 3, "exit_code": 0}],
                  "segments": [{"pid": 10, "segment": "root", "threads": 3, "exit_code": 0},
                               *[{"pid": pid, "segment": f"{pid}-{index}", "threads": 1,
                                  "exit_code": 0 if index == 2 else None}
                                 for pid in (11, 12) for index in range(3)]]}
        self.assertEqual(workloads.topology_errors(report, 10, case), [])
        mutations = {
            "wrong root": lambda value: value.update(root_pid=11),
            "wrong parent": lambda value: value["processes"][2].update(parent_pid=11),
            "wrong parent image": lambda value: value["processes"][1].update(parent_segment="other"),
            "nonzero child exit": lambda value: value["processes"][1].update(exit_code=7),
            "unequal child images": lambda value: (value["processes"][1].update(images=2),
                                                    value["processes"][2].update(images=4)),
            "root exec instead of helper": lambda value: (value["processes"][0].update(images=2),
                                                            value["processes"][1].update(images=2)),
            "misattributed segment": lambda value: value["segments"][1].update(pid=12),
        }
        for label, mutate in mutations.items():
            with self.subTest(mutation=label):
                changed = copy.deepcopy(report)
                mutate(changed)
                self.assertTrue(workloads.topology_errors(changed, 10, case))

    def test_supervisor_exit_zero_does_not_hide_target_or_cleanup_failure(self):
        envelope = {"authority": "diagnostic_only", "integrity_verified": False,
                    "measurement_contract": workloads.collector.measurement_contract(),
                    "security": {"counter_integrity": "application_mutable", "isolation": "external_required"},
                    "execution": {"state": "complete", "exit_code": 0, "cleanup_complete": True,
                                  "records_cleanup_complete": True}}
        self.assertEqual(workloads.envelope_errors(envelope), [])
        for key, value in (("state", "timed_out"), ("exit_code", 7),
                           ("exit_code", False),
                           ("cleanup_complete", False), ("records_cleanup_complete", False)):
            with self.subTest(field=key):
                changed = copy.deepcopy(envelope)
                changed["execution"][key] = value
                self.assertTrue(workloads.envelope_errors(changed))
        envelope["measurement_contract"]["trusted_verdict_eligible"] = True
        self.assertTrue(workloads.envelope_errors(envelope))

    def test_missing_duplicate_and_failed_controls_never_pass_a_matrix(self):
        case = workloads.Workload("probe", "java", [], "", 1, 1)
        rows = [{"workload": "probe", "mode": mode, "passed": True} for mode in workloads.MODES]
        self.assertEqual(workloads.matrix_errors([case], rows), [])
        self.assertTrue(workloads.matrix_errors([case], rows[1:]))
        self.assertTrue(workloads.matrix_errors([case], rows + [rows[0]]))
        rows[0]["passed"] = False
        self.assertTrue(workloads.matrix_errors([case], rows))
        self.assertTrue(workloads.matrix_errors([], []))

    def invoke(self, *, missing=False, changed=False, cleaned=True, wrong_output=False):
        artifacts = self.root / "artifacts"
        for name in ("sdk/bin64/drrun", "sdk/lib64/release/libdynamorio.so", "client", "runner", "python"):
            path = artifacts / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture executable")
            path.chmod(0o700)
        selected_python = artifacts / ("missing" if missing else "python")
        output = self.root / "result"
        args = ["--runtime", "cpython", "--cpython", str(selected_python),
                "--sdk", str(artifacts / "sdk"), "--client", str(artifacts / "client"),
                "--runner-bin", str(artifacts / "runner"), "--output-dir", str(output)]
        changed_once = False

        def case(arguments, workload, mode):
            nonlocal changed_once
            if changed and not changed_once:
                changed_once = True
                (artifacts / "runner").write_bytes(b"replaced during otherwise successful execution")
            return {"workload": workload.name, "runtime": workload.runtime, "mode": mode,
                    "passed": not wrong_output}

        with ExitStack() as stack:
            stack.enter_context(patch.object(workloads, "own_subreaper", return_value=(SimpleNamespace(prctl=lambda *args: 0), 0)))
            stack.enter_context(patch.object(workloads, "cleanup_orphans", return_value=cleaned))
            stack.enter_context(patch.object(workloads, "run_process", return_value={
                "exit_code": 0, "infrastructure_ok": True, "stdout": "Python 3.12.0\n", "stderr": ""}))
            executed = stack.enter_context(patch.object(workloads, "run_case", side_effect=case))
            console = io.StringIO()
            stack.enter_context(redirect_stdout(console))
            code = workloads.main(args)
        report = json.loads((output / "results.json").read_text())
        self.assertEqual(json.loads(console.getvalue())["exit_code"], code)
        return code, report, executed

    def test_missing_selected_runtime_fails_before_any_case_and_writes_report(self):
        code, report, executed = self.invoke(missing=True)
        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        executed.assert_not_called()
        self.assertIn("binary:cpython", [row["input"] for row in report["input_provenance"]["errors"]])

    def test_changed_inputs_invalidate_otherwise_successful_cases(self):
        code, report, _ = self.invoke(changed=True)
        self.assertEqual(code, 1)
        self.assertEqual(report["checks_passed"], 12)
        self.assertFalse(report["inputs_unchanged"])
        self.assertFalse(report["passed"])

    def test_cleanup_failure_invalidates_otherwise_successful_cases(self):
        code, report, _ = self.invoke(cleaned=False)
        self.assertEqual(code, 1)
        self.assertEqual(report["checks_passed"], 12)
        self.assertFalse(report["cleanup_complete"])

    def test_case_failure_invalidates_the_campaign(self):
        code, report, _ = self.invoke(wrong_output=True)
        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])

    def test_wrong_java_major_cannot_proceed_to_fixture_compilation(self):
        args = SimpleNamespace(output_dir=self.root, prlimit="/selected/prlimit", java_major=21)
        with patch.object(workloads, "run_process", return_value={
                "exit_code": 0, "infrastructure_ok": True, "stdout": "",
                "stderr": 'openjdk version "17.0.21"\n'}) as process:
            with self.assertRaisesRegex(ValueError, "required major 21"):
                workloads.probe_and_compile(args, {"java": "/selected/java"}, None)
        self.assertEqual(process.call_count, 1)


if __name__ == "__main__":
    unittest.main()

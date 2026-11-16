"""Require the selected Python implementation before labeling workload results."""
import copy
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import runtime_compat as compat


class PythonImplementationProbeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="durante-python-identity-unit-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.probes = 0

    def directory(self):
        self.probes += 1
        return self.root / f"probe-{self.probes}"

    def observation(self, runtime):
        return {"exit_code": 0, "stdout": runtime + "\n", "stderr": "",
                "infrastructure_ok": True, "timeout": False, "cleanup_complete": True,
                "errors": [], "elapsed_s": 0.01, "root_pid": 17,
                "root_identity": {"pid": 17, "starttime_ticks": 100}}

    def check_probe(self, runtime, observation, passed):
        directory = self.directory()
        original = copy.deepcopy(observation)
        command = ["/selected/python", "-I", "-S", "-c", "import sys; print(sys.implementation.name)"]
        with patch.object(compat, "run_process", return_value=observation) as execute:
            if passed:
                result = compat.probe_python_implementation(
                    runtime, command[0], directory, 1234, "/selected/prlimit")
            else:
                with self.assertRaises(ValueError):
                    compat.probe_python_implementation(
                        runtime, command[0], directory, 1234, "/selected/prlimit")
            execute.assert_called_once_with(command, directory, 1234, "/selected/prlimit")
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        evidence = json.loads((directory / "probe.json").read_text())
        self.assertEqual(evidence["command"], command)
        self.assertEqual(evidence["requested_implementation"], runtime)
        self.assertIs(evidence["passed"], passed)
        self.assertEqual(evidence["execution"], {key: original[key] for key in (
            "exit_code", "stdout", "stderr", "infrastructure_ok", "timeout",
            "cleanup_complete", "errors") if key in original})
        self.assertEqual(observation, original)
        if passed:
            self.assertEqual(result, evidence)
        return evidence

    def test_matching_implementations_preserve_the_probe_and_execution_evidence(self):
        for runtime in ("cpython", "pypy"):
            with self.subTest(runtime=runtime):
                self.check_probe(runtime, self.observation(runtime), True)

    def test_process_identity_and_elapsed_time_do_not_change_successful_probe_evidence(self):
        for runtime in ("cpython", "pypy"):
            with self.subTest(runtime=runtime):
                first = self.check_probe(runtime, self.observation(runtime), True)
                later = self.observation(runtime)
                later.update(elapsed_s=0.95, root_pid=481,
                             root_identity={"pid": 481, "starttime_ticks": 2000})
                second = self.check_probe(runtime, later, True)
                self.assertEqual(first, second)

    def test_each_implementation_rejects_the_other_runtime_name(self):
        for requested, actual in (("cpython", "pypy"), ("pypy", "cpython")):
            with self.subTest(requested=requested, actual=actual):
                self.check_probe(requested, self.observation(actual), False)

    def test_empty_wrong_or_nonexact_output_never_identifies_the_runtime(self):
        for runtime in ("cpython", "pypy"):
            for stdout in ("", "\n", "Python 3.10.16\n", "jython\n", runtime,
                           runtime.upper() + "\n", runtime + "\r\n", " " + runtime + "\n",
                           runtime + " \n", runtime + "\nextra\n", runtime + "\n\x00"):
                with self.subTest(runtime=runtime, stdout=stdout):
                    row = self.observation(runtime)
                    row["stdout"] = stdout
                    self.check_probe(runtime, row, False)

    def test_correct_identity_does_not_hide_diagnostics_exit_or_infrastructure_failure(self):
        changes = (
            {"stderr": "runtime warning\n"},
            {"stderr": " "},
            {"exit_code": 7},
            {"exit_code": -9},
            {"exit_code": None},
            {"exit_code": False},
            {"exit_code": 0.0},
            {"exit_code": "0"},
            {"infrastructure_ok": False},
            {"infrastructure_ok": 1},
            {"infrastructure_ok": "true"},
            {"timeout": True, "infrastructure_ok": False, "errors": ["outer wall deadline expired"]},
            {"cleanup_complete": False, "infrastructure_ok": False, "errors": ["descendant cleanup failed"]},
        )
        for runtime in ("cpython", "pypy"):
            for change in changes:
                with self.subTest(runtime=runtime, change=change):
                    row = self.observation(runtime)
                    row.update(change)
                    self.check_probe(runtime, row, False)

    def test_missing_required_execution_fields_are_recorded_and_rejected(self):
        for runtime in ("cpython", "pypy"):
            for field in ("exit_code", "stdout", "stderr", "infrastructure_ok"):
                with self.subTest(runtime=runtime, field=field):
                    row = self.observation(runtime)
                    del row[field]
                    self.check_probe(runtime, row, False)

    def test_optional_execution_evidence_is_not_invented(self):
        for runtime in ("cpython", "pypy"):
            with self.subTest(runtime=runtime):
                row = {"exit_code": 0, "stdout": runtime + "\n", "stderr": "", "infrastructure_ok": True}
                self.check_probe(runtime, row, True)

    def test_execution_exceptions_propagate_without_success_evidence(self):
        for error in (OSError("injected execution failure"), subprocess.TimeoutExpired("python", 1)):
            with self.subTest(error=type(error).__name__):
                directory = self.directory()
                with patch.object(compat, "run_process", side_effect=error), \
                        self.assertRaises(type(error)) as raised:
                    compat.probe_python_implementation("cpython", "/selected/python", directory, 1000, None)
                self.assertIs(raised.exception, error)
                if (directory / "probe.json").exists():
                    self.assertIs(json.loads((directory / "probe.json").read_text())["passed"], False)

    def test_non_python_runtime_names_are_rejected_before_execution(self):
        for runtime in ("node", "java", "", None):
            with self.subTest(runtime=runtime):
                with patch.object(compat, "run_process") as execute, self.assertRaises(ValueError):
                    compat.probe_python_implementation(runtime, "/selected/python", self.directory(), 1000, None)
                execute.assert_not_called()

    def native_configuration(self):
        runtime = sys.implementation.name
        if runtime not in ("cpython", "pypy"):
            self.skipTest("native identity test requires CPython or PyPy")
        prlimit = compat.executable_path("prlimit")
        if not prlimit:
            self.skipTest("native identity test requires Linux prlimit")
        return runtime, prlimit

    def test_native_current_python_reports_its_real_implementation(self):
        runtime, prlimit = self.native_configuration()
        directory = self.directory()
        evidence = compat.probe_python_implementation(runtime, sys.executable, directory, 2000, prlimit)
        self.assertIs(evidence["passed"], True)
        self.assertEqual(evidence["requested_implementation"], runtime)
        self.assertEqual(evidence, json.loads((directory / "probe.json").read_text()))
        self.assertEqual(evidence["execution"]["stdout"], runtime + "\n")
        self.assertEqual(evidence["execution"]["stderr"], "")
        self.assertEqual(evidence["execution"]["exit_code"], 0)
        self.assertIs(evidence["execution"]["infrastructure_ok"], True)
        self.assertIs(evidence["execution"]["timeout"], False)
        self.assertIs(evidence["execution"]["cleanup_complete"], True)
        self.assertEqual((directory / "stdout.txt").read_bytes(), (runtime + "\n").encode())
        self.assertEqual((directory / "stderr.txt").read_bytes(), b"")

    def test_native_current_python_cannot_be_labeled_as_the_other_implementation(self):
        runtime, prlimit = self.native_configuration()
        requested = "pypy" if runtime == "cpython" else "cpython"
        directory = self.directory()
        with self.assertRaises(ValueError):
            compat.probe_python_implementation(requested, sys.executable, directory, 2000, prlimit)
        evidence = json.loads((directory / "probe.json").read_text())
        self.assertIs(evidence["passed"], False)
        self.assertEqual(evidence["requested_implementation"], requested)
        self.assertEqual(evidence["execution"]["stdout"], runtime + "\n")
        self.assertEqual(evidence["execution"]["exit_code"], 0)
        self.assertIs(evidence["execution"]["infrastructure_ok"], True)

    def native_commands(self, requested, prlimit):
        paths = dict.fromkeys(compat.RUNTIMES)
        paths[requested] = sys.executable
        provenance = compat.InputProvenance({"binary:runtime:" + requested: sys.executable})
        provenance.capture("before")
        self.assertEqual(provenance.errors, [])
        args = SimpleNamespace(timeout_ms=2000, prlimit=prlimit)
        return compat.runtime_commands(args, self.root, paths, None, provenance)

    def test_runtime_commands_preserves_successful_native_identity_in_versions(self):
        runtime, prlimit = self.native_configuration()
        versions, cases = self.native_commands(runtime, prlimit)
        evidence = versions[runtime]["implementation_probe"]
        self.assertIs(evidence["passed"], True)
        self.assertEqual(evidence["execution"]["stdout"], runtime + "\n")
        files = list(self.root.rglob("probe.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(evidence, json.loads(files[0].read_text()))
        self.assertEqual(len(cases), len(compat.PYTHON_FIXTURES))
        self.assertTrue(all(case[1] == runtime for case in cases))

    def test_runtime_commands_rejects_mislabeled_native_python_during_its_probe(self):
        runtime, prlimit = self.native_configuration()
        requested = "pypy" if runtime == "cpython" else "cpython"
        with self.assertRaises(ValueError):
            self.native_commands(requested, prlimit)
        files = list(self.root.rglob("probe.json"))
        self.assertEqual(len(files), 1)
        evidence = json.loads(files[0].read_text())
        self.assertIs(evidence["passed"], False)
        self.assertEqual(evidence["requested_implementation"], requested)
        self.assertEqual(evidence["execution"]["stdout"], runtime + "\n")
        self.assertEqual(evidence["execution"]["exit_code"], 0)


if __name__ == "__main__":
    unittest.main()

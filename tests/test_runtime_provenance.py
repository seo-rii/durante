"""Input changes must invalidate qualification, including successful fixtures."""
from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import runtime_compat as runtime


class RuntimeProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sources = self.root / "source"
        self.fixture_dir = self.sources / "tests/runtime_fixtures"
        names = ["tests/runtime_compat.py", "tests/test_runtime_compat.py",
                 "tests/test_runtime_provenance.py", "tests/concurrent_spawn.py",
                 "tests/fixtures.py", "collect.py", "tests/runtime_fixtures/RuntimeProbe.java",
                 "tests/runtime_fixtures/arithmetic.js",
                 *("tests/runtime_fixtures/" + name + ".py" for name, _, _ in runtime.PYTHON_FIXTURES)]
        for name in names:
            path = self.sources / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture input " + name)
        self.artifacts = {
            "core": self.root / "sdk/lib64/release/libdynamorio.so",
            "drrun": self.root / "sdk/bin64/drrun",
            "client": self.root / "client.so", "runner": self.root / "durante",
            "runtime:cpython": self.root / "python", "runtime:java": self.root / "java",
            "javac": self.root / "javac",
        }
        for name, path in self.artifacts.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture binary " + name)
            path.chmod(0o700)
        self.calls = []

    def probe(self, command, directory, timeout_ms, prlimit):
        self.calls.append((command, prlimit))
        return {"exit_code": 0, "infrastructure_ok": True, "stdout": "version\n", "stderr": ""}

    def successful_case(self, args, work, name, language, target, expected, processes, index):
        return {"runtime": language, "fixture": name, "status": "passed", "rows": []}

    def invoke(self, case=None, probe=None):
        output = self.root / ("result-" + str(len(list(self.root.glob("result-*")))))
        args = ["--sdk", str(self.root / "sdk"), "--client", str(self.artifacts["client"]),
                "--runner-bin", str(self.artifacts["runner"]), "--output-dir", str(output),
                "--cpython", str(self.artifacts["runtime:cpython"]), "--pypy", str(self.root / "missing-pypy"),
                "--node", str(self.root / "missing-node"), "--java", str(self.artifacts["runtime:java"]),
                "--javac", str(self.artifacts["javac"]), "--require-qualified-runtime", "java"]
        with ExitStack() as stack:
            stack.enter_context(patch.object(runtime, "ROOT", self.sources))
            stack.enter_context(patch.object(runtime, "FIXTURES", self.fixture_dir))
            stack.enter_context(patch.object(runtime, "__file__", str(self.sources / "tests/runtime_compat.py")))
            stack.enter_context(patch.object(runtime, "own_subreaper", return_value=(SimpleNamespace(prctl=lambda *args: 0), 0)))
            stack.enter_context(patch.object(runtime, "cleanup_orphans", return_value=True))
            process = stack.enter_context(patch.object(runtime, "run_process", side_effect=probe or self.probe))
            stack.enter_context(patch.object(runtime, "run_case", side_effect=case or self.successful_case))
            console = io.StringIO()
            stack.enter_context(redirect_stdout(console))
            code = runtime.main(args)
        report = json.loads((output / "results.json").read_text())
        self.assertEqual(json.loads(console.getvalue())["exit_code"], code)
        self.assertEqual(report["exit_code"], code)
        return code, report, process

    def test_unchanged_inputs_include_launcher_compiler_and_selected_runtime_paths(self):
        code, report, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertTrue(report["inputs_unchanged"])
        self.assertTrue(report["sources_unchanged"])
        self.assertEqual(report["missing_runtimes"], ["pypy", "node"])
        files = report["input_provenance"]["files"]
        for name, path in self.artifacts.items():
            record = files["binary:" + name]
            self.assertEqual(record["path"], str(path))
            self.assertEqual(record["before"], record["after"])
            self.assertTrue(record["unchanged"])
            self.assertEqual(report["binary_sha256"][name], hashlib.sha256(path.read_bytes()).hexdigest())
        for command, prlimit in self.calls:
            self.assertIn(command[0], [files["binary:" + name]["path"]
                                      for name in ("runtime:cpython", "runtime:java", "javac")])
            self.assertEqual(prlimit, files["binary:prlimit"]["path"])
        self.assertIn(str(self.artifacts["javac"]), [command[0] for command, _ in self.calls])

    def test_successful_fixtures_cannot_hide_replaced_changed_or_deleted_inputs(self):
        inputs = {"binary:" + name: path for name, path in self.artifacts.items()}
        inputs["source:tests/runtime_fixtures/arithmetic.py"] = self.fixture_dir / "arithmetic.py"
        for name, path in inputs.items():
            original = path.read_bytes()
            for mutation in ("replace", "change", "delete"):
                with self.subTest(input=name, mutation=mutation):
                    path.write_bytes(original)
                    path.chmod(0o700)
                    changed = False

                    def case(*args):
                        nonlocal changed
                        if not changed:
                            changed = True
                            if mutation == "replace":
                                replacement = path.with_name(path.name + ".new")
                                replacement.write_bytes(original)
                                replacement.chmod(0o700)
                                replacement.replace(path)
                            elif mutation == "delete":
                                path.unlink()
                            else:
                                path.write_bytes(original + b"changed")
                        return self.successful_case(*args)

                    code, report, _ = self.invoke(case=case)
                    self.assertEqual(code, 1)
                    self.assertFalse(report["inputs_unchanged"])
                    self.assertIn(name, [error["input"] for error in report["input_provenance"]["errors"]])
                    record = report["input_provenance"]["files"][name]
                    self.assertFalse(record["unchanged"])
                    if mutation == "replace":
                        self.assertEqual(record["before"]["sha256"], record["after"]["sha256"])
                        self.assertNotEqual(record["before"]["file"]["inode"], record["after"]["file"]["inode"])
            path.write_bytes(original)
            path.chmod(0o700)

    def test_initial_missing_required_input_has_json_and_launches_nothing(self):
        for name in ("core", "drrun", "client", "runner"):
            with self.subTest(input=name):
                path = self.artifacts[name]
                original = path.read_bytes()
                path.unlink()
                code, report, process = self.invoke()
                self.assertEqual(code, 1)
                process.assert_not_called()
                self.assertIn({"input": "binary:" + name, "phase": "before"},
                              [{key: error[key] for key in ("input", "phase")}
                               for error in report["input_provenance"]["errors"]])
                self.assertIsNotNone(report["fatal_error"])
                path.write_bytes(original)
                path.chmod(0o700)

    def test_missing_required_java_compiler_is_reported_as_unavailable(self):
        self.artifacts["javac"].unlink()
        code, report, _ = self.invoke()
        self.assertEqual(code, 1)
        self.assertIn("java", report["missing_runtimes"])
        self.assertIn("java", report["required_unqualified"])

    def test_snapshot_precedes_version_probes_and_compilation(self):
        for name in ("runtime:cpython", "javac"):
            with self.subTest(input=name):
                path = self.artifacts[name]
                original = path.read_bytes()

                def probe(command, *args):
                    if command[0] == str(path):
                        path.write_bytes(original + b"changed while executing")
                    return self.probe(command, *args)

                code, report, _ = self.invoke(probe=probe)
                self.assertEqual(code, 1)
                self.assertFalse(report["input_provenance"]["files"]["binary:" + name]["unchanged"])
                path.write_bytes(original)

    def test_uncheckable_final_identity_is_explicit_failure(self):
        original_identity = runtime.file_identity
        finished = False

        def case(*args):
            nonlocal finished
            finished = True
            return self.successful_case(*args)

        def identity(path):
            if finished and path == str(self.artifacts["drrun"]):
                raise PermissionError("fixture denied inspection")
            return original_identity(path)

        with patch.object(runtime, "file_identity", side_effect=identity):
            code, report, _ = self.invoke(case=case)
        self.assertEqual(code, 1)
        error = next(error for error in report["input_provenance"]["errors"] if error["input"] == "binary:drrun")
        self.assertEqual(error["phase"], "after")
        self.assertIn("denied inspection", error["reason"])

    def test_identity_bounds_reject_nonregular_and_oversized_inputs(self):
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        large = self.root / "large"
        with large.open("wb") as stream:
            stream.truncate(runtime.MAX_INPUT_BYTES + 1)
        for path in (fifo, large, self.root):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    runtime.file_identity(path)

    def test_symlink_retargeting_and_replacement_are_visible(self):
        first, second = self.root / "first", self.root / "second"
        first.write_bytes(b"same contents")
        second.write_bytes(b"same contents")
        alias = self.root / "alias"
        for target in (first, second):
            alias.symlink_to(first)
            provenance = runtime.InputProvenance({"runtime": alias})
            provenance.capture("before")
            replacement = self.root / "replacement-alias"
            replacement.symlink_to(target)
            replacement.replace(alias)
            provenance.capture("after")
            self.assertFalse(provenance.unchanged())
            alias.unlink()

    def test_selected_absolute_path_is_executed_after_path_environment_changes(self):
        prlimit = shutil.which("prlimit")
        if not prlimit:
            self.skipTest("Linux prlimit unavailable")
        first, second = self.root / "first-bin", self.root / "second-bin"
        for directory, text in ((first, "first"), (second, "second")):
            directory.mkdir()
            executable = directory / "runtime"
            executable.write_text("#!/bin/sh\nprintf '" + text + "\\n'\n")
            executable.chmod(0o700)
        output = self.root / "actual-process"
        output.mkdir()
        with patch.dict(os.environ, {"PATH": str(first)}):
            selected = runtime.executable_path("runtime")
        with patch.dict(os.environ, {"PATH": str(second)}):
            row = runtime.run_process([selected], output, 1000, prlimit)
        self.assertEqual(row["stdout"], "first\n")
        self.assertTrue(row["infrastructure_ok"])

    def test_engine_modes_use_the_recorded_launcher_and_limiter(self):
        args = SimpleNamespace(sdk=self.root / "unselected-sdk", drrun=self.artifacts["drrun"],
                               client=self.artifacts["client"], prlimit="/selected/prlimit", timeout_ms=1000)

        def process(*args):
            return {"stdout": "expected\n", "exit_code": 0, "timeout": False,
                    "cleanup_complete": True, "infrastructure_ok": True, "errors": [], "root_pid": 123}

        with patch.object(runtime, "run_process", side_effect=process) as calls, \
                patch.object(runtime, "collect_records", return_value={"valid": True, "unsupported_rep_entries": 0}), \
                patch.object(runtime, "inspect_accounting", return_value=([], False)):
            result = runtime.run_case(args, self.root, "probe", "cpython", [str(self.artifacts["runtime:cpython"])],
                                      "expected\n", 1, 0)
        self.assertEqual(result["status"], "passed")
        for call in calls.call_args_list[1:]:
            self.assertEqual(call.args[0][0], str(args.drrun))
            self.assertIn("-disable_traces", call.args[0])
            self.assertEqual(call.args[3], args.prlimit)

    def test_collector_uses_the_recorded_runner_and_limiter(self):
        args = SimpleNamespace(runner_bin=self.artifacts["runner"], prlimit="/selected/prlimit")

        def collect(command, **kwargs):
            kwargs["stdout"].write(b'{"valid":false,"complete":false}\n')
            return SimpleNamespace(returncode=1)

        with patch.object(runtime.subprocess, "run", side_effect=collect) as calls:
            result = runtime.collect_records(args, self.root, 123)
        self.assertFalse(result["valid"])
        command = calls.call_args.args[0]
        self.assertEqual(command[0], args.prlimit)
        self.assertEqual(command[command.index("--") + 1], str(args.runner_bin))


if __name__ == "__main__":
    unittest.main()

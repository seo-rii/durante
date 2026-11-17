"""Validate explicit PyPy hash inputs without assuming CPython startup flags."""
from contextlib import nullcontext, redirect_stdout
import copy
from dataclasses import asdict
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import runtime_compat
import runtime_repeatability as repeatability
from test_runtime_repeatability import (collection_fixture, controlled_collection_fixture,
                                       host, observation, required_inputs)


def pypy_collection(name="seed-1", hash_flag=0):
    collection = collection_fixture("pypy")
    profile = collection["profile"]
    files = collection["input_provenance"]["files"]
    executable = profile["runtime_executable"]
    cases = repeatability.profile_cases(profile, files)
    cases += repeatability.python_startup_cases("pypy", executable)
    cases = repeatability.controlled_cases(cases, name, runtime="pypy")
    seed = "1" if name == "seed-1" else "random"
    personality = {"value": 0, "addr_no_randomize": False}
    for location in (profile["host"], collection["host_before"], collection["host_after"]):
        location["process_personality"] = copy.deepcopy(personality)
    effective = {"executable": executable, "implementation": "pypy",
                 "flags": {"isolated": 0, "ignore_environment": 0, "no_user_site": 1,
                           "no_site": 1, "hash_randomization": hash_flag},
                 "python_environment": {"PYTHONHASHSEED": seed}, "hash_sentinel": -123456789,
                 "hash_algorithm": "synthetic-siphash", "stdlib_paths": ["/runtime/pathlib.py"],
                 "sys_path": ["", "/runtime/lib_pypy", "/runtime/lib-python/3"],
                 "working_directory": "/unit/pypy-startup-probe", "process_personality": personality}
    profile.update(pypy_hash_profile=name, pypy_probe_version=1, pypy_effective=effective,
                   workload_names=[case.name for case in cases], workloads=[asdict(case) for case in cases],
                   environment={**repeatability.FIXED_ENV, "PYTHONHASHSEED": seed})
    collection["pypy_probe"] = {
        "command": [executable, "-s", "-S", "-c", repeatability.PYPY_PROBE],
        "directory": effective["working_directory"], "stdout": json.dumps(effective), "stderr": "",
        "exit_code": 0, "errors": [], "timeout": False, "infrastructure_ok": True,
        "cleanup_complete": True, "python_hash_seed": seed}
    for phase in ("before", "after"):
        files["binary:runtime"][phase]["resolved_path"] = executable
    files["probe:pypy-module:0"] = {"path": "/runtime/pathlib.py",
        "before": {"sha256": "b" * 64}, "after": {"sha256": "b" * 64}, "unchanged": True}
    profile["input_sha256"]["probe:pypy-module:0"] = "b" * 64
    collection["plan"] = repeatability.fixed_plan(cases, 3)
    by_name = {case.name: case for case in cases}
    collection["rows"] = [{**item, **observation(by_name[item["workload"]], item["mode"], profile,
                                                Path("/unit") / str(index)), "python_hash_seed": seed}
                          for index, item in enumerate(collection["plan"])]
    collection["profile_sha256"] = repeatability.digest(profile)
    return collection


class PyPyHashControlTests(unittest.TestCase):
    def test_named_profiles_reconstruct_exact_pypy_commands_after_checkout_relocation(self):
        profiles = []
        self.assertEqual(repeatability.PYPY_CONTROL_FLAGS, ("-s", "-S"))
        self.assertEqual(repeatability.PYPY_HASH_PROFILES, {"isolated": None, "random": "random", "seed-1": "1"})
        self.assertEqual(repeatability.PYPY_HASH_WORKLOADS, ("allocation-gc", "startup", "imports"))
        for name in ("random", "seed-1"):
            with self.subTest(profile=name):
                collection = pypy_collection(name)
                before = copy.deepcopy(collection)
                expected = repeatability.evaluate(collection)
                self.assertTrue(expected["qualification_passed"], expected)
                with patch.object(repeatability.workloads, "FIXTURES", Path("/other-checkout/fixtures")):
                    observed = repeatability.evaluate(collection)
                    cases = repeatability.profile_cases(collection["profile"], collection["input_provenance"]["files"])
                self.assertEqual(observed, expected)
                self.assertEqual(collection, before)
                self.assertEqual(len(collection["plan"]), 36)
                self.assertEqual([case.name for case in cases], ["pypy-allocation-gc", "pypy-startup", "pypy-imports"])
                original = repeatability.workloads.workload_cases({"pypy": "/runtime/pypy3"}, Path("/unit/classes"))[1:2]
                original += repeatability.python_startup_cases("pypy", "/runtime/pypy3")
                for changed, isolated in zip(cases, original):
                    self.assertEqual(changed.command, [isolated.command[0], "-s", "-S", *isolated.command[3:]])
                    self.assertEqual((changed.expected, changed.processes, changed.images),
                                     (isolated.expected, 1, 1))
                profiles.append(collection["profile"])
        self.assertEqual(profiles[0]["workloads"], profiles[1]["workloads"])
        environments = [dict(profile["environment"]) for profile in profiles]
        self.assertEqual([environment.pop("PYTHONHASHSEED") for environment in environments], ["random", "1"])
        self.assertEqual(environments[0], environments[1])

    def test_hash_randomization_flag_zero_and_one_are_both_valid_pypy_evidence(self):
        for name in ("random", "seed-1"):
            for flag in (0, 1):
                with self.subTest(profile=name, flag=flag):
                    result = repeatability.evaluate(pypy_collection(name, flag))
                    self.assertTrue(result["qualification_passed"], result)

    def test_probe_fields_are_validated_even_when_stored_and_observed_json_agree(self):
        changes = {
            "wrong implementation": lambda value: value.update(implementation="cpython"),
            "wrong executable": lambda value: value.update(executable="/runtime/python3"),
            "ignored environment": lambda value: value["flags"].update(ignore_environment=1),
            "isolation still enabled": lambda value: value["flags"].update(isolated=1),
            "site enabled": lambda value: value["flags"].update(no_site=0),
            "user site enabled": lambda value: value["flags"].update(no_user_site=0),
            "unknown flag": lambda value: value["flags"].update(safe_path=True),
            "unknown hash flag": lambda value: value["flags"].update(hash_randomization=2),
            "boolean hash flag": lambda value: value["flags"].update(hash_randomization=False),
            "wrong seed": lambda value: value["python_environment"].update(PYTHONHASHSEED="random"),
            "additional Python input": lambda value: value["python_environment"].update(PYTHONPATH="/other"),
            "boolean sentinel": lambda value: value.update(hash_sentinel=True),
            "overflow sentinel": lambda value: value.update(hash_sentinel=1 << 63),
            "empty algorithm": lambda value: value.update(hash_algorithm=""),
            "missing modules": lambda value: value.update(stdlib_paths=[]),
            "relative module": lambda value: value.update(stdlib_paths=["pathlib.py"]),
            "duplicate modules": lambda value: value.update(stdlib_paths=["/runtime/pathlib.py"] * 2),
            "unsorted modules": lambda value: value.update(stdlib_paths=["/runtime/z.py", "/runtime/a.py"]),
            "empty sys.path": lambda value: value.update(sys_path=[]),
            "missing current directory slot": lambda value: value.update(sys_path=["/runtime"]),
            "relative sys.path": lambda value: value.update(sys_path=["", "relative"]),
            "different directory": lambda value: value.update(working_directory="/other"),
            "personality mismatch": lambda value: value.update(process_personality={"value": 0x40000, "addr_no_randomize": True}),
        }
        for label, mutate in changes.items():
            with self.subTest(mutation=label):
                collection = pypy_collection()
                mutate(collection["profile"]["pypy_effective"])
                collection["pypy_probe"]["stdout"] = json.dumps(collection["profile"]["pypy_effective"])
                collection["profile_sha256"] = repeatability.digest(collection["profile"])
                self.assertFalse(repeatability.evaluate(collection)["collection_passed"])

    def test_required_probe_version_seed_directory_and_module_evidence_cannot_be_omitted_or_changed(self):
        for change in ("missing probe", "missing version", "unknown version", "boolean version", "missing effective",
                       "wrong command", "stderr", "exit", "timeout", "cleanup", "probe seed", "row seed",
                       "directory", "missing module", "module path", "module identity", "JSON mismatch"):
            with self.subTest(mutation=change):
                collection = pypy_collection()
                profile, probe = collection["profile"], collection["pypy_probe"]
                files = collection["input_provenance"]["files"]
                if change == "missing probe":
                    collection.pop("pypy_probe")
                elif change == "missing version":
                    profile.pop("pypy_probe_version")
                elif change in ("unknown version", "boolean version"):
                    profile["pypy_probe_version"] = 2 if change == "unknown version" else True
                elif change == "missing effective":
                    profile.pop("pypy_effective")
                elif change == "wrong command":
                    probe["command"].insert(1, "-I")
                elif change == "stderr":
                    probe["stderr"] = "warning\n"
                elif change == "exit":
                    probe["exit_code"] = 7
                elif change == "timeout":
                    probe["timeout"] = True
                elif change == "cleanup":
                    probe["cleanup_complete"] = False
                elif change == "probe seed":
                    probe["python_hash_seed"] = "random"
                elif change == "row seed":
                    collection["rows"][0]["python_hash_seed"] = "random"
                elif change == "directory":
                    probe["directory"] = "/other"
                elif change == "missing module":
                    files.pop("probe:pypy-module:0")
                    profile["input_sha256"].pop("probe:pypy-module:0")
                elif change == "module path":
                    files["probe:pypy-module:0"]["path"] = "/other/pathlib.py"
                elif change == "module identity":
                    files["probe:pypy-module:0"]["after"]["sha256"] = "c" * 64
                else:
                    probe["stdout"] = "{}"
                collection["profile_sha256"] = repeatability.digest(profile)
                self.assertFalse(repeatability.evaluate(collection)["collection_passed"])

    def test_legacy_isolation_and_cpython_controls_keep_their_existing_contract(self):
        for runtime in ("cpython", "pypy"):
            with self.subTest(runtime=runtime):
                collection = collection_fixture(runtime)
                self.assertEqual(repeatability.hash_profile(collection["profile"]), "isolated")
                self.assertTrue(repeatability.evaluate(collection)["qualification_passed"])
                self.assertNotIn("PYTHONHASHSEED", repeatability.profile_environment(collection["profile"]))
                cases = repeatability.profile_cases(collection["profile"], collection["input_provenance"]["files"])
                self.assertEqual(cases[0].command[1:3], ["-I", "-S"])
                collection["pypy_probe"] = {"stdout": "{}"}
                self.assertFalse(repeatability.evaluate(collection)["collection_passed"])
        for name in ("random", "seed-1"):
            self.assertTrue(repeatability.evaluate(controlled_collection_fixture(name))["qualification_passed"])
        self.assertEqual(repeatability.CPYTHON_CONTROL_FLAGS, ("-P", "-s", "-S"))

    def test_foreign_simultaneous_and_unsupported_controls_fail_before_setup(self):
        invalid_profiles = (
            {"runtime": "cpython", "pypy_hash_profile": "seed-1"},
            {"runtime": "pypy", "cpython_hash_profile": "seed-1"},
            {"runtime": "node", "pypy_hash_profile": "random"},
            {"runtime": "pypy", "pypy_hash_profile": "random", "cpython_hash_profile": "seed-1"},
            {"runtime": "pypy", "pypy_hash_profile": "unknown"},
        )
        for profile in invalid_profiles:
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                repeatability.hash_profile(profile)
        for runtime, selected, cpython, pypy in (
                ("cpython", ["allocation-gc"], "isolated", "seed-1"),
                ("pypy", ["allocation-gc"], "seed-1", "random"),
                ("node", ["allocation-gc"], "isolated", "random"),
                ("pypy", None, "isolated", "seed-1"),
                ("pypy", ["threads"], "isolated", "seed-1"),
                ("pypy", ["processes"], "isolated", "random"),
                ("pypy", ["imports", "processes"], "isolated", "seed-1")):
            with self.subTest(runtime=runtime, workloads=selected), self.assertRaises(ValueError):
                repeatability.collect(SimpleNamespace(runtime=runtime, workload=selected,
                    cpython_hash_profile=cpython, pypy_hash_profile=pypy))
        ordinary = repeatability.workloads.workload_cases({"pypy": "/runtime/pypy3"}, Path("/classes"))
        for case in (ordinary[0], ordinary[2]):
            with self.subTest(workload=case.name), self.assertRaises(ValueError):
                repeatability.controlled_cases([case], "seed-1", runtime="pypy")

    def test_cli_keeps_isolation_by_default_and_accepts_explicit_pypy_hash_control(self):
        result = {"collection_complete": True, "collection_passed": True,
                  "qualification_passed": True, "failures": []}
        for name in (None, "random", "seed-1"):
            with self.subTest(profile=name):
                command = ["collect", "--runtime", "pypy", "--workload", "imports", "--sdk", "/sdk",
                           "--client", "/client", "--runner-bin", "/runner", "--output-dir", "/results"]
                if name is not None:
                    command += ["--pypy-hash-profile", name]
                with patch.object(repeatability, "collect", return_value=result) as collect, redirect_stdout(io.StringIO()):
                    self.assertEqual(repeatability.main(command), 0)
                args = collect.call_args.args[0]
                self.assertEqual(args.pypy_hash_profile, name or "isolated")
                self.assertEqual(args.cpython_hash_profile, "isolated")

    def test_real_workload_runner_forwards_hash_seed_only_for_explicit_python_controls(self):
        with tempfile.TemporaryDirectory(prefix="durante-hash-forwarding-") as temporary:
            root = Path(temporary)
            for runtime in ("pypy", "cpython", "node", "java"):
                for index, seed in enumerate(("random", "1", None, "missing")):
                    with self.subTest(runtime=runtime, seed=seed):
                        directory = root / (runtime + str(index))
                        directory.mkdir()
                        args = SimpleNamespace(output_dir=directory, timeout_ms=1000, prlimit="/prlimit")
                        if seed != "missing":
                            args.python_hash_seed = seed
                        case = repeatability.workloads.workload_cases({runtime: "/runtime/executable"}, root)[0]
                        execution = {"root_pid": 1, "exit_code": 0, "stdout": case.expected,
                                     "stderr": "", "infrastructure_ok": True, "errors": []}
                        with patch.object(repeatability.workloads, "run_process", return_value=execution) as run:
                            row = repeatability.workloads.run_case(args, case, "native")
                        self.assertTrue(row["passed"], row)
                        run.assert_called_once()
                        if runtime in ("cpython", "pypy") and seed in ("random", "1"):
                            self.assertEqual(run.call_args.kwargs["python_hash_seed"], seed)
                        else:
                            self.assertNotIn("python_hash_seed", run.call_args.kwargs)

    def test_collection_runs_one_native_probe_for_the_whole_repeated_campaign(self):
        with tempfile.TemporaryDirectory(prefix="durante-pypy-hash-unit-") as temporary:
            root = Path(temporary)
            binary, module = root / "runtime", root / "pathlib.py"
            binary.write_text("synthetic runtime identity")
            module.write_text("synthetic module identity")
            args = SimpleNamespace(runtime="pypy", runtime_bin=str(binary), runtime_file=[], runtime_image=None,
                pypy_hash_profile="seed-1", cpython_hash_profile="isolated", workload=["startup", "imports"],
                sdk=root, client=binary, runner_bin=binary, output_dir=root / "results", javac=str(binary),
                java_major=17, cpu=2, repeats=3, max_spread_ppm=0, timeout_ms=1000, campaign_seconds=600)
            inputs = {key: binary for key in required_inputs("pypy")}
            inputs["source:tests/runtime_workload_fixtures/workload.py"] = repeatability.workloads.FIXTURES / "workload.py"
            effective = copy.deepcopy(pypy_collection()["profile"]["pypy_effective"])
            effective.update(executable=str(binary), stdlib_paths=[str(module)], sys_path=["", str(root)],
                             working_directory=str(args.output_dir / "pypy-startup-probe"))
            probe_row = {"stdout": json.dumps(effective), "stderr": "", "exit_code": 0, "errors": [],
                         "infrastructure_ok": True, "cleanup_complete": True, "timeout": False, "python_hash_seed": "1"}
            location = host()
            location["process_personality"] = effective["process_personality"]

            def run_case(options, case, mode):
                profile = repeatability.read_collection(args.output_dir / "collection.json")["profile"]
                return {**observation(case, mode, profile, options.output_dir / (case.name + "-" + mode)),
                        "python_hash_seed": options.python_hash_seed}

            with patch.object(repeatability, "executable_path", return_value=str(binary)), \
                    patch.object(repeatability, "input_paths", return_value=inputs), \
                    patch.object(repeatability, "host_profile", return_value=location), \
                    patch.object(repeatability.os, "sched_getaffinity", return_value={2}), \
                    patch.object(repeatability.os, "sched_setaffinity"), \
                    patch.object(repeatability, "own_subreaper", return_value=(SimpleNamespace(prctl=lambda *args: 0), 0)), \
                    patch.object(repeatability, "cleanup_orphans", return_value=True), \
                    patch.object(repeatability, "fixed_environment", return_value=nullcontext()), \
                    patch.object(repeatability.workloads, "probe_and_compile", return_value=({"pypy": {"path": str(binary), "version": "unit"}}, root)), \
                    patch.object(repeatability.workloads, "run_process", return_value=probe_row) as probe, \
                    patch.object(repeatability.workloads, "run_case", side_effect=run_case) as execute:
                result = repeatability.collect(args)
            self.assertTrue(result["qualification_passed"], result)
            probe.assert_called_once_with([str(binary), "-s", "-S", "-c", repeatability.PYPY_PROBE],
                args.output_dir / "pypy-startup-probe", 10000, str(binary), python_hash_seed="1")
            self.assertEqual(execute.call_count, 24)
            recorded = repeatability.read_collection(args.output_dir / "collection.json")
            self.assertEqual(recorded["profile"]["pypy_probe_version"], 1)
            self.assertNotIn("cpython_probe", recorded)
            self.assertEqual(recorded["pypy_probe"]["stdout"], json.dumps(effective))

    def test_native_pypy_fixed_seed_repeats_and_random_profile_reports_its_inputs(self):
        explicit = "DURANTE_TEST_PYPY" in os.environ
        selected = os.environ.get("DURANTE_TEST_PYPY") if explicit else "pypy3"
        executable = shutil.which(selected) if selected else None
        if not executable:
            if explicit:
                self.fail("explicit DURANTE_TEST_PYPY is unavailable or not executable")
            self.skipTest("native PyPy hash probe requires pypy3 or DURANTE_TEST_PYPY")
        prlimit = runtime_compat.executable_path("prlimit")
        self.assertIsNotNone(prlimit, "native PyPy hash probe requires Linux prlimit")
        executable = str(Path(executable).absolute())
        value = int(Path("/proc/self/personality").read_text().strip(), 16)
        personality = {"value": value, "addr_no_randomize": bool(value & repeatability.ADDR_NO_RANDOMIZE)}
        observed = []
        with tempfile.TemporaryDirectory(prefix="durante-native-pypy-hash-") as temporary:
            for index, name in enumerate(("seed-1", "seed-1", "random", "random")):
                directory = Path(temporary) / str(index)
                directory.mkdir()
                profile = {"runtime": "pypy", "runtime_executable": executable, "pypy_hash_profile": name,
                           "host": {"process_personality": personality}}
                collection = {"profile": profile}
                provenance = SimpleNamespace(files={"binary:runtime": {"before": runtime_compat.file_identity(executable)}})
                seed = "1" if name == "seed-1" else "random"
                args = SimpleNamespace(output_dir=directory, prlimit=prlimit, python_hash_seed=seed)
                with repeatability.fixed_environment():
                    repeatability.collect_pypy_probe(args, collection, provenance)
                effective = profile["pypy_effective"]
                self.assertEqual(profile["pypy_probe_version"], 1)
                self.assertEqual(effective["implementation"], "pypy")
                self.assertEqual(effective["python_environment"], {"PYTHONHASHSEED": seed})
                self.assertEqual(effective["executable"], str(Path(executable).resolve()))
                self.assertEqual(effective["working_directory"], collection["pypy_probe"]["directory"])
                self.assertEqual(effective["process_personality"], personality)
                self.assertEqual({key: effective["flags"][key] for key in ("isolated", "ignore_environment", "no_user_site", "no_site")},
                                 {"isolated": 0, "ignore_environment": 0, "no_user_site": 1, "no_site": 1})
                self.assertIs(type(effective["flags"]["hash_randomization"]), int)
                self.assertIn(effective["flags"]["hash_randomization"], (0, 1))
                self.assertNotIn("safe_path", effective["flags"])
                self.assertIs(type(effective["hash_sentinel"]), int)
                self.assertLessEqual(-(1 << 63), effective["hash_sentinel"])
                self.assertLess(effective["hash_sentinel"], 1 << 63)
                self.assertTrue(effective["hash_algorithm"])
                self.assertTrue(effective["stdlib_paths"])
                self.assertEqual(effective["stdlib_paths"], sorted(set(effective["stdlib_paths"])))
                self.assertEqual(effective["sys_path"][0], "")
                self.assertTrue(all(Path(path).is_absolute() for path in effective["sys_path"][1:]))
                self.assertEqual(repeatability.probe_errors(collection, profile, provenance.files), [])
                observed.append(effective)
        self.assertEqual(observed[0]["hash_sentinel"], observed[1]["hash_sentinel"])
        # Randomized hashes may collide, so their inequality is not an oracle.


if __name__ == "__main__":
    unittest.main()

"""Repeatability gates use complete fixed plans and exact integer statistics."""
from contextlib import nullcontext
import copy
from dataclasses import asdict
from fractions import Fraction
import json
import os
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import runtime_repeatability as repeatability
import runtime_compat


def host():
    return {"system": "Linux", "machine": "x86_64", "kernel": "unit-only",
            "affinity": [2], "cpus": [{"processor": "2", "flags": "sse sse2"}]}


def required_inputs():
    return repeatability.REQUIRED_INPUTS | {
        "source:tests/runtime_repeatability.py", "source:tests/runtime_workloads.py",
        "source:tests/runtime_compat.py", "source:collect.py",
        "source:tests/runtime_workload_fixtures/workload.py"}


def measured(rep):
    vectors = json.loads((repeatability.ROOT / "tests/protocol_vectors.json").read_text())["cases"]
    name = "rep-breakdown-single" if rep else "rep-breakdown-disabled"
    case = next(row for row in vectors if row["name"] == name)
    return repeatability.workloads.collector.aggregate(
        {int(name.removeprefix("process-").removesuffix(".jsonl")): rows for name, rows in case["files"].items()}, case["root_pid"])


def observation(case, mode, profile, directory):
    row = {"state": "complete", "passed": True, "infrastructure_ok": True,
           "cleanup_complete": True, "exit_code": 0, "timeout": False,
           "stdout": case.expected, "stderr": "", "errors": [],
           "directory": str(directory), "command": repeatability.expected_command(profile, case, mode, directory)}
    if mode in repeatability.METERED:
        row["measurement"] = measured(mode == "supervisor")
    if mode == "supervisor":
        row["report"] = {"authority": "diagnostic_only", "integrity_verified": False,
                         "measurement_contract": repeatability.workloads.collector.measurement_contract(),
                         "security": {"counter_integrity": "application_mutable", "isolation": "external_required"},
                         "execution": {"state": "complete", "exit_code": 0, "cleanup_complete": True,
                                       "records_cleanup_complete": True},
                         "measurement": copy.deepcopy(row["measurement"])}
    return row


def collection_fixture():
    case = next(case for case in repeatability.workloads.workload_cases({"cpython": "/runtime/python3"}, Path("/unit/classes")) if case.name == "cpython-allocation-gc")
    profile = {"runtime": "cpython", "runtime_executable": "/runtime/python3", "classes_directory": "/unit/classes",
               "workload_names": [case.name], "workloads": [asdict(case)], "execution_profile": "run",
               "environment": repeatability.FIXED_ENV, "fresh_processes": True,
               "runtime_image": None, "versions": {"cpython": {"path": "/runtime/python3", "version": "synthetic unit fixture"}},
               "runner": "/runtime/durante", "drrun": "/runtime/drrun", "client": "/runtime/client.so",
               "timeout_ms": 1000, "host": host(), "input_sha256": {key: "a" * 64 for key in required_inputs()}}
    identity = {"sha256": "a" * 64}
    files = {key: {"before": copy.deepcopy(identity), "after": copy.deepcopy(identity), "unchanged": True}
             for key in required_inputs()}
    plan = repeatability.fixed_plan([case], 3)
    rows = [{**item, **observation(case, item["mode"], profile, Path("/unit") / str(index))}
            for index, item in enumerate(plan)]
    return {"schema": 1, "authority": "diagnostic_only", "profile": profile,
            "profile_sha256": repeatability.digest(profile), "options": {"repeats": 3, "max_spread_ppm": 0},
            "plan": plan, "rows": rows, "setup_complete": True, "cleanup_complete": True,
            "host_before": host(), "host_after": host(), "inputs_unchanged": True,
            "input_provenance": {"files": files, "errors": []}, "failures": []}



def controlled_collection_fixture(name):
    collection = collection_fixture()
    profile = collection["profile"]
    case = repeatability.controlled_cases(repeatability.profile_cases(profile), name)[0]
    profile["cpython_hash_profile"] = name
    profile["workloads"] = [asdict(case)]
    profile["environment"] = repeatability.profile_environment(profile)
    seed = repeatability.CPYTHON_HASH_PROFILES[name]
    effective = {"executable": "/runtime/python3", "flags": {
        "isolated": 0, "ignore_environment": 0, "safe_path": True,
        "no_user_site": 1, "no_site": 1, "hash_randomization": 1},
        "python_environment": {"PYTHONHASHSEED": seed}, "hash_sentinel": -123456789,
        "hash_algorithm": "synthetic-hash", "stdlib_paths": ["/runtime/pathlib.py"]}
    profile["cpython_effective"] = effective
    collection["cpython_probe"] = {"command": ["/runtime/python3", *repeatability.CPYTHON_CONTROL_FLAGS, "-c", repeatability.CPYTHON_PROBE],
        "stdout": json.dumps(effective), "stderr": "", "exit_code": 0, "errors": [],
        "timeout": False, "infrastructure_ok": True, "cleanup_complete": True, "python_hash_seed": seed}
    files = collection["input_provenance"]["files"]
    for phase in ("before", "after"):
        files["binary:runtime"][phase]["resolved_path"] = "/runtime/python3"
    files["probe:cpython-module:0"] = {"path": "/runtime/pathlib.py",
        "before": {"sha256": "b" * 64}, "after": {"sha256": "b" * 64}, "unchanged": True}
    profile["input_sha256"]["probe:cpython-module:0"] = "b" * 64
    for row in collection["rows"]:
        row["python_hash_seed"] = seed
        row["command"] = repeatability.expected_command(profile, case, row["mode"], Path(row["directory"]))
    collection["profile_sha256"] = repeatability.digest(profile)
    return collection


def startup_collection_fixture():
    collection = controlled_collection_fixture("seed-1")
    profile = collection["profile"]
    cases = repeatability.controlled_cases(repeatability.cpython_startup_cases(profile["runtime_executable"]), "seed-1")
    profile["workload_names"] = [case.name for case in cases]
    profile["workloads"] = [asdict(case) for case in cases]
    collection["plan"] = repeatability.fixed_plan(cases, 3)
    by_name = {case.name: case for case in cases}
    collection["rows"] = [{**item, **observation(by_name[item["workload"]], item["mode"], profile, Path("/unit") / str(index)),
                           "python_hash_seed": "1"}
                          for index, item in enumerate(collection["plan"])]
    collection["profile_sha256"] = repeatability.digest(profile)
    return collection


def change_counter(row, name, value):
    report = row["measurement"]
    for item in [report, *report["processes"], *report["segments"]]:
        item[name] = value
    if "report" in row:
        row["report"]["measurement"] = copy.deepcopy(report)


class RepeatabilityTests(unittest.TestCase):
    def test_exact_large_integer_statistics_and_fractional_median(self):
        large = (1 << 64) - 3
        summary = repeatability.count_spread([large, large + 1, large + 2], 1)
        self.assertEqual(summary["median"], {"numerator": large + 1, "denominator": 1})
        expected = Fraction(2_000_000, large + 1)
        self.assertEqual(summary["spread_ppm"], repeatability.fraction_json(expected))
        self.assertTrue(summary["passed"])
        even = repeatability.count_spread([1, 2, 3, 4], 1_000_000)
        self.assertEqual(even["median"], {"numerator": 5, "denominator": 2})
        self.assertEqual(even["spread_ppm"], {"numerator": 1_200_000, "denominator": 1})
        self.assertFalse(even["passed"])

    def test_exact_threshold_and_zero_median(self):
        values = [999_999, 1_000_000, 1_000_001]
        self.assertTrue(repeatability.count_spread(values, 2)["passed"])
        self.assertFalse(repeatability.count_spread(values, 1)["passed"])
        self.assertTrue(repeatability.count_spread([0, 0, 0], 0)["passed"])
        result = repeatability.count_spread([0, 0, 1], 1_000_000)
        self.assertIsNone(result["spread_ppm"])
        self.assertFalse(result["passed"])
        for bad in (True, 1.0, -1, 1 << 64, "1"):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                repeatability.count_spread([bad], 0)

    def test_full_fixed_collection_passes_without_mutating_evidence(self):
        collection = collection_fixture()
        before = copy.deepcopy(collection)
        result = repeatability.evaluate(collection)
        self.assertTrue(result["collection_complete"], result)
        self.assertTrue(result["collection_passed"], result)
        self.assertTrue(result["qualification_passed"], result)
        self.assertEqual(len(result["groups"]), 2)
        self.assertEqual(collection, before)

    def test_successful_collection_does_not_imply_repeatability(self):
        collection = collection_fixture()
        row = collection["rows"][-1]
        change_counter(row, "instructions", row["measurement"]["instructions"] + 1)
        result = repeatability.evaluate(collection)
        self.assertTrue(result["collection_complete"], result)
        self.assertTrue(result["collection_passed"], result)
        self.assertFalse(result["qualification_passed"], result)
        group = next(group for group in result["groups"] if group["mode"] == "supervisor")
        self.assertEqual(group["metrics"]["instructions"]["range"], 1)
        self.assertEqual(group["observed_samples"], 3)

    def test_split_variation_is_visible_when_combined_rep_is_constant(self):
        collection = collection_fixture()
        row = collection["rows"][-1]
        report = row["measurement"]
        for item in [report, *report["processes"], *report["segments"]]:
            item["rep_breakdown"]["movs_bytes"] += 1
            item["rep_breakdown"]["stos_bytes"] -= 1
        row["report"]["measurement"] = copy.deepcopy(report)
        result = repeatability.evaluate(collection)
        self.assertTrue(result["collection_passed"], result)
        self.assertFalse(result["qualification_passed"], result)
        metrics = result["groups"][-1]["metrics"]
        self.assertTrue(metrics["rep_bytes"]["passed"])
        self.assertFalse(metrics["movs_bytes"]["passed"])

    def test_missing_duplicate_skip_and_failure_never_become_subset_statistics(self):
        for label in ("missing", "duplicate", "unrun", "failed", "complete failure", "boolean repeat"):
            with self.subTest(label=label):
                collection = collection_fixture()
                if label == "missing":
                    collection["rows"].pop()
                elif label == "duplicate":
                    collection["rows"][-1] = copy.deepcopy(collection["rows"][-5])
                elif label == "boolean repeat":
                    collection["rows"][0]["repeat"] = True
                else:
                    collection["rows"][-1]["state"] = "complete" if label == "complete failure" else label
                    collection["rows"][-1]["passed"] = False
                    collection["rows"][-1]["errors"] = ["retained failed observation"]
                result = repeatability.evaluate(collection)
                self.assertFalse(result["collection_passed"], result)
                self.assertFalse(result["qualification_passed"], result)
                for group in result["groups"]:
                    if group["mode"] == "supervisor":
                        self.assertEqual(group["metrics"], {})

    def test_legacy_missing_split_and_invalid_partitions_do_not_qualify(self):
        for label, edit in {
            "legacy": lambda report: report.pop("rep_breakdown"),
            "boolean": lambda report: report.update(instructions=True),
            "float": lambda report: report["rep_breakdown"].update(movs_bytes=1.0),
            "overflow": lambda report: report["rep_breakdown"].update(movs_bytes=1 << 64),
            "partition": lambda report: report["rep_breakdown"].update(movs_bytes=999),
            "process sum": lambda report: report["processes"][0].update(instructions=999),
            "incomplete": lambda report: report.update(complete=False),
            "promoted": lambda report: report.update(integrity_verified=True),
        }.items():
            with self.subTest(label=label):
                collection = collection_fixture()
                row = collection["rows"][-1]
                edit(row["measurement"])
                row["report"]["measurement"] = copy.deepcopy(row["measurement"])
                result = repeatability.evaluate(collection)
                self.assertFalse(result["collection_passed"], result)
                self.assertFalse(result["qualification_passed"], result)

    def test_profile_and_actual_command_changes_fail_even_with_stable_counts(self):
        edits = {
            "command": lambda data: data["rows"][-1]["command"].append("--changed"),
            "isa": lambda data: data["host_after"]["cpus"][0].update(flags="sse"),
            "affinity": lambda data: data["host_after"].update(affinity=[3]),
            "missing binary": lambda data: data["input_provenance"]["files"].pop("binary:runtime"),
            "changed binary": lambda data: data["input_provenance"]["files"]["binary:runtime"]["after"].update(sha256="b" * 64),
            "JIT option": lambda data: data["profile"]["workloads"][0]["command"].insert(1, "-Xint"),
            "environment": lambda data: data["profile"].update(environment={}),
            "cleanup": lambda data: data.update(cleanup_complete=False),
        }
        for label, edit in edits.items():
            with self.subTest(label=label):
                collection = collection_fixture()
                edit(collection)
                result = repeatability.evaluate(collection)
                self.assertFalse(result["qualification_passed"], result)

    def test_named_hash_profiles_change_only_explicit_seed_and_keep_default_commands(self):
        isolated = collection_fixture()
        self.assertEqual(repeatability.profile_cases(isolated["profile"])[0].command[1:3], ["-I", "-S"])
        controlled = [controlled_collection_fixture(name) for name in ("random", "seed-1")]
        for collection in controlled:
            before = copy.deepcopy(collection)
            result = repeatability.evaluate(collection)
            self.assertTrue(result["qualification_passed"], result)
            self.assertEqual(collection, before)
            self.assertEqual(len(collection["plan"]), 12)
            self.assertEqual(collection["options"]["max_spread_ppm"], 0)
        profiles = [item["profile"] for item in controlled]
        self.assertEqual(profiles[0]["workloads"], profiles[1]["workloads"])
        environments = [dict(profile["environment"]) for profile in profiles]
        self.assertEqual([env.pop("PYTHONHASHSEED") for env in environments], ["random", "1"])
        self.assertEqual(environments[0], environments[1])
        for runtime, workloads in (("java", ["allocation-gc"]), ("cpython", ["processes"]), ("cpython", None)):
            with self.subTest(runtime=runtime, workloads=workloads), self.assertRaises(ValueError):
                repeatability.collect(SimpleNamespace(runtime=runtime, workload=workloads, cpython_hash_profile="seed-1"))

    def test_controlled_profile_requires_effective_probe_seed_and_module_identity(self):
        for label in ("missing probe", "ignored seed", "other Python input", "missing module", "missing forwarded seed"):
            with self.subTest(label=label):
                collection = controlled_collection_fixture("seed-1")
                profile = collection["profile"]
                if label == "missing probe":
                    collection.pop("cpython_probe")
                elif label == "ignored seed":
                    profile["cpython_effective"]["flags"]["ignore_environment"] = 1
                    collection["cpython_probe"]["stdout"] = json.dumps(profile["cpython_effective"])
                elif label == "other Python input":
                    profile["environment"]["PYTHONPATH"] = "/unexpected"
                elif label == "missing module":
                    collection["input_provenance"]["files"].pop("probe:cpython-module:0")
                    profile["input_sha256"].pop("probe:cpython-module:0")
                else:
                    collection["rows"][0].pop("python_hash_seed")
                collection["profile_sha256"] = repeatability.digest(profile)
                self.assertFalse(repeatability.evaluate(collection)["qualification_passed"])

    def test_startup_controls_keep_separate_complete_groups_and_fixed_commands(self):
        collection = startup_collection_fixture()
        before = copy.deepcopy(collection)
        result = repeatability.evaluate(collection)
        self.assertTrue(result["qualification_passed"], result)
        self.assertEqual(collection, before)
        self.assertEqual(len(collection["plan"]), 24)
        self.assertEqual({(group["workload"], group["mode"]) for group in result["groups"]},
                         {(name, mode) for name in ("cpython-startup", "cpython-imports") for mode in repeatability.METERED})
        for name in ("cpython-startup", "cpython-imports"):
            changed = copy.deepcopy(collection)
            row = next(row for row in changed["rows"] if row["workload"] == name and row["mode"] == "supervisor")
            change_counter(row, "instructions", row["measurement"]["instructions"] + 1)
            observed = repeatability.evaluate(changed)
            self.assertTrue(observed["collection_passed"], observed)
            self.assertFalse(observed["qualification_passed"])
            failed = [group for group in observed["groups"] if not group["passed"]]
            self.assertEqual([(group["workload"], group["mode"]) for group in failed], [(name, "supervisor")])
        changed = copy.deepcopy(collection)
        changed["profile"]["workloads"][0]["command"][-1] += "# changed inline program\n"
        changed["profile_sha256"] = repeatability.digest(changed["profile"])
        self.assertFalse(repeatability.evaluate(changed)["collection_passed"])

    def test_startup_controls_are_cpython_only_and_do_not_enter_default_workloads(self):
        ordinary = repeatability.workloads.workload_cases({"cpython": "/runtime/python"}, Path("/classes"))
        self.assertEqual([case.name for case in ordinary],
                         ["cpython-threads", "cpython-allocation-gc", "cpython-processes"])
        for runtime in ("java", "node"):
            for workload in ("startup", "imports"):
                with self.subTest(runtime=runtime, workload=workload), self.assertRaisesRegex(ValueError, "require cpython"):
                    repeatability.collect(SimpleNamespace(runtime=runtime, workload=[workload], cpython_hash_profile="isolated"))

    def test_native_startup_and_import_controls_have_exact_outputs_and_one_process(self):
        prlimit = repeatability.executable_path("prlimit")
        if not prlimit:
            self.skipTest("Linux prlimit unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            for name in ("isolated", "seed-1"):
                cases = repeatability.controlled_cases(repeatability.cpython_startup_cases(sys.executable), name)
                for case in cases:
                    directory = Path(temporary) / (name + case.name)
                    directory.mkdir()
                    with repeatability.fixed_environment():
                        row = runtime_compat.run_process(case.command, directory, 10000, prlimit,
                                                         python_hash_seed=repeatability.CPYTHON_HASH_PROFILES[name])
                    self.assertEqual(row["exit_code"], 0, row)
                    self.assertEqual(row["stdout"], case.expected)
                    self.assertEqual(row["stderr"], "")
                    self.assertTrue(row["cleanup_complete"])
                    self.assertEqual((case.processes, case.images, case.minimum_threads), (1, 1, 1))

    def test_all_executed_harness_helpers_are_required_offline(self):
        for name in ("source:tests/concurrent_spawn.py", "source:tests/fixtures.py"):
            collection = collection_fixture()
            collection["input_provenance"]["files"].pop(name)
            collection["profile"]["input_sha256"].pop(name)
            collection["profile_sha256"] = repeatability.digest(collection["profile"])
            self.assertFalse(repeatability.evaluate(collection)["collection_passed"])

    def test_process_environment_discards_inherited_python_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            for seed in (None, "random", "1"):
                directory = Path(temporary) / str(seed)
                directory.mkdir()
                child = SimpleNamespace(pid=123, wait=lambda **kwargs: 0, poll=lambda: 0)
                with patch.dict(os.environ, {"PATH": "/bin", "PYTHONHASHSEED": "99", "PYTHONPATH": "/inherited", "PYTHONMALLOC": "debug"}, clear=True), \
                        patch.object(runtime_compat.subprocess, "Popen", return_value=child) as spawn, \
                        patch.object(runtime_compat, "cleanup_orphans", return_value=True):
                    row = runtime_compat.run_process(["/runtime/python"], directory, 1000, "/prlimit", python_hash_seed=seed)
                environment = spawn.call_args.kwargs["env"]
                python = {key: value for key, value in environment.items() if key.startswith("PYTHON")}
                self.assertEqual(python, {} if seed is None else {"PYTHONHASHSEED": seed})
                self.assertEqual(row.get("python_hash_seed"), seed)
            with self.assertRaises(ValueError):
                runtime_compat.run_process([], Path(temporary), 1000, "/prlimit", python_hash_seed="2")

    def test_native_probe_confirms_default_isolation_and_fixed_seed_options(self):
        prlimit = repeatability.executable_path("prlimit")
        if not prlimit:
            self.skipTest("Linux prlimit unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            observations = []
            for index, flags in enumerate((['-I', '-S'], list(repeatability.CPYTHON_CONTROL_FLAGS), list(repeatability.CPYTHON_CONTROL_FLAGS))):
                directory = Path(temporary) / str(index)
                directory.mkdir()
                with repeatability.fixed_environment():
                    row = runtime_compat.run_process([sys.executable, *flags, '-c', repeatability.CPYTHON_PROBE], directory, 10000, prlimit, python_hash_seed="1")
                self.assertEqual(row["exit_code"], 0, row)
                self.assertEqual(row["stderr"], "")
                observations.append(json.loads(row["stdout"]))
            self.assertEqual(observations[0]["flags"]["isolated"], 1)
            self.assertEqual(observations[0]["flags"]["ignore_environment"], 1)
            for observed in observations[1:]:
                self.assertEqual(observed["flags"]["isolated"], 0)
                self.assertEqual(observed["flags"]["ignore_environment"], 0)
                self.assertEqual(observed["python_environment"], {"PYTHONHASHSEED": "1"})
                self.assertTrue(observed["stdlib_paths"])
            self.assertEqual(observations[1]["hash_sentinel"], observations[2]["hash_sentinel"])

    def test_parser_rejects_duplicate_json_and_preserves_integer_precision(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "collection.json"
            path.write_text('{"instructions":9007199254740993}')
            self.assertEqual(repeatability.read_collection(path)["instructions"], (1 << 53) + 1)
            for contents in ('{"schema":1,"schema":1}', '{"x":NaN}', '{"x":Infinity}'):
                path.write_text(contents)
                with self.assertRaises(ValueError):
                    repeatability.read_collection(path)

    def collect_mocked(self, outcome):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            dummy = directory / "identity"
            dummy.write_bytes(b"unit fixture")
            args = SimpleNamespace(sdk=directory, client=dummy, runner_bin=dummy, output_dir=directory / "results",
                                   runtime="cpython", runtime_bin=str(dummy), runtime_file=[], runtime_image=None,
                                   javac=str(dummy), java_major=17, cpu=2, workload=["allocation-gc"], repeats=3,
                                   max_spread_ppm=0, timeout_ms=1000, campaign_seconds=600)
            calls = []

            def run_case(options, case, mode):
                calls.append(mode)
                if outcome == "exception":
                    raise OSError("unit observation exception")
                return {"command": ["mocked"], "passed": False, "errors": ["recorded failure"],
                        "infrastructure_ok": outcome != "cleanup", "cleanup_complete": outcome != "cleanup", "exit_code": 7,
                        "timeout": False, "stdout": "", "stderr": ""}

            with patch.object(repeatability, "executable_path", return_value=str(dummy)), \
                    patch.object(repeatability, "input_paths", return_value={key: dummy for key in required_inputs()}), \
                    patch.object(repeatability, "host_profile", side_effect=lambda cpus: host()), \
                    patch.object(repeatability.os, "sched_getaffinity", return_value={2}), \
                    patch.object(repeatability.os, "sched_setaffinity"), \
                    patch.object(repeatability, "own_subreaper", return_value=(SimpleNamespace(prctl=lambda *args: 0), 0)), \
                    patch.object(repeatability, "cleanup_orphans", return_value=True), \
                    patch.object(repeatability, "fixed_environment", return_value=nullcontext()), \
                    patch.object(repeatability.workloads, "probe_and_compile", return_value=({"cpython": {"path": str(dummy), "version": "unit"}}, directory)), \
                    patch.object(repeatability.workloads, "run_case", side_effect=run_case):
                result = repeatability.collect(args)
            recorded = repeatability.read_collection(args.output_dir / "collection.json")
            self.assertTrue((args.output_dir / "evaluation.json").is_file())
            return calls, recorded, result

    def test_collector_records_failed_attempt_and_continues_without_retry(self):
        calls, recorded, result = self.collect_mocked("program failure")
        self.assertEqual(calls, list(repeatability.workloads.MODES) * 3)
        self.assertEqual(len(recorded["rows"]), 12)
        self.assertTrue(result["collection_complete"])
        self.assertFalse(result["collection_passed"])
        self.assertTrue(all(row["exit_code"] == 7 for row in recorded["rows"]))

    def test_cleanup_failure_keeps_all_remaining_attempts_unrun(self):
        calls, recorded, result = self.collect_mocked("cleanup")
        self.assertEqual(calls, ["native"])
        self.assertEqual(len(recorded["rows"]), 12)
        self.assertEqual(recorded["rows"][0]["state"], "complete")
        self.assertFalse(result["collection_complete"])
        self.assertFalse(result["qualification_passed"])
        for row in recorded["rows"][1:]:
            self.assertEqual(row["state"], "unrun")
            self.assertEqual(row["errors"], ["prior observation descendant cleanup incomplete"])

    def test_harness_exception_preserves_failed_attempt_and_unrun_plan(self):
        calls, recorded, result = self.collect_mocked("exception")
        self.assertEqual(calls, ["native"])
        self.assertEqual(recorded["rows"][0]["state"], "failed")
        self.assertIn("unit observation exception", recorded["rows"][0]["errors"][0])
        self.assertFalse(result["collection_complete"])
        self.assertFalse(result["qualification_passed"])
        for row in recorded["rows"][1:]:
            self.assertEqual(row["state"], "unrun")
            self.assertEqual(row["errors"], ["prior observation could not complete"])


if __name__ == "__main__":
    unittest.main()

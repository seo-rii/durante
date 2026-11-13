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
    def runtime_paths(self):
        return {"java": "/selected/java", "cpython": "/selected/python", "node": "/selected/node"}

    def cgroup_files(self, root):
        contents = {
            "memory.current": "2048\n",
            "memory.peak": "8192\n",
            "memory.max": "1073741824\n",
            "memory.events": "low 0\nhigh 1\nmax 2\noom 3\noom_kill 4\noom_group_kill 0\n",
            "pids.current": "3\n",
            "pids.max": "128\n",
            "pids.events": "max 5\n",
            "cpu.max": "200000 100000\n",
            "cpu.stat": "usage_usec 100\nuser_usec 60\nsystem_usec 40\nnr_periods 8\nnr_throttled 2\nthrottled_usec 10\n",
        }
        for name, text in contents.items():
            (root / name).write_text(text)

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

    def test_default_suite_preserves_all_ten_workloads_and_both_profiles(self):
        paths, classes = self.runtime_paths(), Path("/classes")
        cases = isolated.workload_plan(paths, classes, "all")
        self.assertEqual(cases, isolated.workload_cases(paths, classes))
        self.assertEqual({case.name for case in cases}, {
            "java-threads", "java-allocation-gc", "java-processes-posix_spawn", "java-processes-fork",
            "cpython-threads", "cpython-allocation-gc", "cpython-processes",
            "node-threads", "node-allocation-gc", "node-processes",
        })
        self.assertEqual(len(cases), 10)
        self.assertEqual(isolated.SUITE_PROFILES["all"], ("installation", "relocated-bundle"))
        self.assertEqual(len(cases) * len(isolated.SUITE_PROFILES["all"]) * 2, 40)

    def test_java_child_plan_interleaves_launch_mechanisms_with_unchanged_oracles(self):
        paths, classes = self.runtime_paths(), Path("/classes")
        cases = isolated.workload_plan(paths, classes, "java-children")
        expected_names = [f"java-processes-{launch}-repeat-{repeat}"
                          for repeat in (1, 2, 3) for launch in ("posix_spawn", "fork")]
        self.assertEqual([case.name for case in cases], expected_names)
        self.assertEqual(isolated.SUITE_PROFILES["java-children"], ("relocated-bundle",))
        originals = {case.name: case for case in isolated.workload_cases(paths, classes)}
        for case in cases:
            with self.subTest(case=case.name):
                original = originals[case.name.rsplit("-repeat-", 1)[0]]
                self.assertEqual(case.command, original.command)
                self.assertEqual(case.expected, "processes:204\n")
                self.assertEqual(case.runtime, "java")
                self.assertEqual(case.processes, 3)
                self.assertEqual(case.images, 7 if "posix_spawn" in case.name else 5)
                self.assertEqual(case.minimum_threads, original.minimum_threads)

    def test_child_suite_requires_java_and_both_launch_mechanisms(self):
        paths, classes = self.runtime_paths(), Path("/classes")
        with self.assertRaises(ValueError):
            isolated.workload_plan({"cpython": "/selected/python"}, classes, "java-children")
        cases = isolated.workload_cases(paths, classes)
        for omitted in ("java-processes-posix_spawn", "java-processes-fork"):
            with self.subTest(omitted=omitted):
                with patch.object(isolated, "workload_cases", return_value=[case for case in cases if case.name != omitted]):
                    with self.assertRaises(ValueError):
                        isolated.workload_plan(paths, classes, "java-children")

    def test_every_planned_child_repeat_and_mode_must_complete_successfully(self):
        cases = isolated.workload_plan(self.runtime_paths(), Path("/classes"), "java-children")
        rows = [{"workload": case.name, "mode": mode, "passed": True}
                for case in cases for mode in ("native", "isolate")]
        self.assertEqual(len(rows), 12)
        self.assertEqual(isolated.matrix_errors(cases, rows), [])
        for index in range(len(rows)):
            with self.subTest(case=rows[index]):
                self.assertTrue(isolated.matrix_errors(cases, rows[:index] + rows[index + 1:]))
                self.assertTrue(isolated.matrix_errors(cases, rows + [rows[index]]))
                failed = copy.deepcopy(rows)
                failed[index]["passed"] = False
                self.assertTrue(isolated.matrix_errors(cases, failed))
        self.assertTrue(isolated.matrix_errors(cases, rows[:4] * 3))

    def test_resource_snapshot_preserves_usage_limits_and_complete_counters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.cgroup_files(root)
            snapshot = isolated.resource_snapshot(root)
            self.assertEqual(snapshot, {
                "memory_current_bytes": 2048, "memory_peak_bytes": 8192,
                "memory_max": "1073741824", "pids_current": 3, "pids_max": "128",
                "cpu_max": "200000 100000",
                "memory_events": {"low": 0, "high": 1, "max": 2, "oom": 3, "oom_kill": 4, "oom_group_kill": 0},
                "pids_events": {"max": 5},
                "cpu_stat": {"usage_usec": 100, "user_usec": 60, "system_usec": 40,
                             "nr_periods": 8, "nr_throttled": 2, "throttled_usec": 10},
            })
            (root / "memory.max").write_text("max\n")
            (root / "pids.max").write_text("max\n")
            unlimited = isolated.resource_snapshot(root)
            self.assertEqual(unlimited["memory_max"], "max")
            self.assertEqual(unlimited["pids_max"], "max")

    def test_missing_optional_peak_is_explicit_and_missing_required_evidence_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.cgroup_files(root)
            (root / "memory.peak").unlink()
            self.assertIsNone(isolated.resource_snapshot(root)["memory_peak_bytes"])
            for name in ("memory.current", "memory.max", "memory.events", "pids.current",
                         "pids.max", "pids.events", "cpu.max", "cpu.stat"):
                with self.subTest(missing=name):
                    content = (root / name).read_text()
                    (root / name).unlink()
                    with self.assertRaises(OSError):
                        isolated.resource_snapshot(root)
                    (root / name).write_text(content)

    def test_resource_events_require_nonnegative_complete_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.cgroup_files(root)
            required = {"memory.events": ("max", "oom", "oom_kill"),
                        "pids.events": ("max",),
                        "cpu.stat": ("usage_usec", "user_usec", "system_usec")}
            for name, keys in required.items():
                original = (root / name).read_text()
                for key in keys:
                    for value in (None, "-1", "unavailable"):
                        with self.subTest(file=name, key=key, value=value):
                            lines = [line for line in original.splitlines() if line.split()[0] != key]
                            if value is not None:
                                lines.append(f"{key} {value}")
                            (root / name).write_text("\n".join(lines) + "\n")
                            with self.assertRaises(ValueError):
                                isolated.resource_snapshot(root)
                (root / name).write_text(original)

    def test_full_matrix_keeps_resource_evidence_out_of_the_bounded_owner_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cgroup = root / "cgroup"
            cgroup.mkdir()
            self.cgroup_files(cgroup)
            # These additional CPU counters occur in the retained CI reports.
            (cgroup / "cpu.stat").write_text(
                "usage_usec 123456789\nuser_usec 110000000\nsystem_usec 13456789\n"
                "core_sched.force_idle_usec 0\nnr_periods 1000\nnr_throttled 15\n"
                "throttled_usec 1000000\nnr_bursts 0\nburst_usec 0\n")
            before = isolated.resource_snapshot(cgroup)
            after = copy.deepcopy(before)
            after["cpu_stat"].update(usage_usec=124456789, user_usec=110900000, system_usec=13556789)
            resources = {"before": before, "after": after, "delta": isolated.resource_delta(before, after)}
            paths = {"java": "/usr/lib/jvm/java-21-openjdk-amd64/bin/java",
                     "cpython": "/usr/bin/python3.13", "node": "/usr/lib/durante-node/bin/node"}
            cases = isolated.workload_plan(paths, Path("/evidence/input/classes"), "all")
            rows, summaries = [], []
            for case in cases:
                for mode in ("native", "isolate"):
                    name = case.name + "-" + mode
                    target = ["/evidence/input/" + Path(arg).name
                              if arg.startswith(str(isolated.ROOT / "tests/runtime_workload_fixtures"))
                              else arg for arg in case.command]
                    if mode == "native":
                        command = ["/usr/bin/prlimit", "--as=1073741824", "--cpu=121",
                                   "--fsize=16777216", "--core=0", "--", *target]
                    else:
                        command = ["/relocated version/bin/durante-bundle", "isolate",
                                   "--workspace", "/evidence/input", "--report", f"/evidence/{name}/report.json",
                                   "--output-dir", f"/evidence/{name}/captured", "--output-limit-bytes", "65536",
                                   "--timeout-ms", "120000", "--",
                                   *[arg.replace("/evidence/input", "/work") for arg in target]]
                    row = {"workload": case.name, "runtime": case.runtime, "mode": mode,
                           "command": command, "exit_code": 0, "stdout": case.expected if mode == "native" else "",
                           "stderr": "", "elapsed_seconds": 60.123, "cleanup_complete": True,
                           "errors": [], "passed": True, "resources": copy.deepcopy(resources)}
                    if not rows:
                        row.update(stdout="wrong\n", errors=["native exact-output control failed"], passed=False)
                    rows.append(row)
                    original = copy.deepcopy(row)
                    evidence = root / name
                    evidence.mkdir()
                    summary = isolated.record_observation(evidence, row)
                    self.assertEqual(row, original)
                    self.assertNotIn("resources", summary)
                    self.assertEqual(summary["resource_evidence"], name + "/result.json")
                    detail = isolated.read_json(root / summary["resource_evidence"])
                    for key, value in row.items():
                        self.assertEqual(detail[key], value)
                        if key != "resources":
                            self.assertEqual(summary[key], value)
                    summaries.append(summary)
            self.assertEqual(len(rows), 20)
            worker = {"schema": 1, "profile": "relocated-bundle", "suite": "all", "cases": 20,
                      "passed": False, "cleanup_complete": True,
                      "planned_observations": [{"workload": row["workload"], "mode": row["mode"]} for row in rows],
                      "unrun_observations": [], "rows": rows,
                      "doctor": {"checks_passed": True, "execution_tested": False,
                                 "measurement_contract": isolated.collector.measurement_contract(),
                                 "checks": [{"id": name, "status": "pass", "required": True} for name in (
                                     "close_range_cloexec", "drrun_installation", "client_installation", "sdk_manifest",
                                     "isolation_launcher", "cli_installation", "namespace_interfaces",
                                     "user_namespace_limit", "cgroup_limits")]}}
            duplicated = root / "duplicated-worker.json"
            isolated.publish(duplicated, worker)
            self.assertGreater(duplicated.stat().st_size, 65536)
            with self.assertRaisesRegex(ValueError, "oversized evidence"):
                isolated.read_json(duplicated)
            compact = root / "worker.json"
            isolated.publish(compact, {**worker, "rows": summaries})
            self.assertLessEqual(compact.stat().st_size, 65536)
            loaded = isolated.read_json(compact)
            self.assertEqual(loaded["rows"], summaries)
            self.assertEqual(isolated.matrix_errors(cases, loaded["rows"]), isolated.matrix_errors(cases, rows))
            self.assertTrue(isolated.matrix_errors(cases, loaded["rows"]))

    def test_resource_delta_distinguishes_new_events_from_prior_campaign_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.cgroup_files(root)
            before = isolated.resource_snapshot(root)
            after = copy.deepcopy(before)
            after["memory_events"].update(max=5, oom=4, oom_kill=5)
            after["pids_events"]["max"] = 7
            after["cpu_stat"].update(usage_usec=160, user_usec=90, system_usec=70,
                                     nr_periods=12, nr_throttled=3, throttled_usec=15)
            after["memory_current_bytes"] = 1024
            after["pids_current"] = 1
            self.assertEqual(isolated.resource_delta(before, after), {
                "memory_events": {"low": 0, "high": 0, "max": 3, "oom": 1, "oom_kill": 1, "oom_group_kill": 0},
                "pids_events": {"max": 2},
                "cpu_stat": {"usage_usec": 60, "user_usec": 30, "system_usec": 30,
                             "nr_periods": 4, "nr_throttled": 1, "throttled_usec": 5},
            })
            unchanged = isolated.resource_delta(before, before)
            self.assertTrue(all(value == 0 for counters in unchanged.values() for value in counters.values()))

    def test_missing_new_or_decreasing_event_counters_cannot_be_valid_deltas(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.cgroup_files(root)
            before = isolated.resource_snapshot(root)
            for section in ("memory_events", "pids_events", "cpu_stat"):
                for mutation in ("missing", "new", "decreasing"):
                    with self.subTest(section=section, mutation=mutation):
                        after = copy.deepcopy(before)
                        key = next(key for key, value in after[section].items() if value > 0)
                        if mutation == "missing":
                            del after[section][key]
                        elif mutation == "new":
                            after[section]["new_counter"] = 0
                        else:
                            after[section][key] -= 1
                        with self.assertRaises(ValueError):
                            isolated.resource_delta(before, after)

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

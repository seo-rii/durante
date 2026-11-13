import copy
import errno
import signal
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from execution_owner import (OOM_MEMORY, PUBLICATION, case_errors, cgroup_state,
                             exhaust_destination, has_accepted_counts, memory_snapshot,
                             stop_owned_container, stopped_fixture, unified_cgroup)


class ExecutionOwnerTests(unittest.TestCase):
    def test_fault_injection_needs_actual_running_controller_and_verified_teardown(self):
        observed = {"controller_pid": 12, "fixture_ready": True,
                    "fault": "controller-kill", "exit_code": -signal.SIGKILL}
        self.assertEqual(case_errors("controller-kill", observed, None, True), [])
        for changes, cleanup in (({"fixture_ready": False}, True),
                                 ({"exit_code": 0}, True), ({}, False)):
            with self.subTest(changes=changes, cleanup=cleanup):
                self.assertTrue(case_errors("controller-kill", observed | changes, None, cleanup))

    def test_suspended_controller_requires_observed_stop(self):
        observed = {"controller_pid": 12, "fixture_ready": True,
                    "fault": "controller-stop", "controller_stopped": True}
        self.assertEqual(case_errors("controller-stop", observed, None, True), [])
        observed["controller_stopped"] = False
        self.assertTrue(case_errors("controller-stop", observed, None, True))

    def test_failure_does_not_accept_partial_or_successful_inner_counts(self):
        observed = {"controller_pid": 12, "fixture_ready": True,
                    "fault": "controller-kill", "exit_code": -signal.SIGKILL}
        for report in ({"untrusted_inner_report": {}}, {"measurement": {"valid": True}},
                       {"measurement": {"valid": False, "instructions": 4}}):
            with self.subTest(report=report):
                self.assertTrue(has_accepted_counts(report))
                self.assertTrue(case_errors("controller-kill", observed, report, True))

    def test_malformed_report_is_rejected_without_crashing_owner(self):
        observed = {"controller_pid": 12, "fixture_ready": True,
                    "fault": "controller-kill", "exit_code": -signal.SIGKILL}
        for report in ([], {"execution": []}, {"measurement": 4},
                       {"untrusted_inner_report": {"measurement": []}},
                       {"untrusted_inner_report": {"execution": []}}):
            with self.subTest(report=report):
                self.assertTrue(case_errors("controller-kill", observed, report, True))

    def test_positive_control_cannot_pass_on_unavailable_installation(self):
        observed = {"controller_pid": 12, "exit_code": 0, "stdout": "owner-success\n"}
        report = {"authority": "diagnostic_only", "integrity_verified": False,
                  "execution": {"state": "complete", "cleanup_complete": True,
                                "scratch_cleanup_complete": True},
                  "untrusted_inner_report": {"measurement": {"valid": True, "complete": True},
                                             "execution": {"exit_code": 0}}}
        self.assertEqual(case_errors("success", observed, report, True), [])
        self.assertTrue(case_errors("success", observed, None, True))
        invalid = copy.deepcopy(report)
        invalid["untrusted_inner_report"]["measurement"]["valid"] = False
        self.assertTrue(case_errors("success", observed, invalid, True))

    def test_resource_faults_need_quota_evidence(self):
        pids = {"controller_pid": 12, "fault": "pids", "pids_events_max": 1,
                "stdout": "owner-pids-exhausted:20\n"}
        self.assertEqual(case_errors("pids", pids, None, True), [])
        self.assertTrue(case_errors("pids", pids | {"pids_events_max": 0}, None, True))
        storage = {"controller_pid": 12, "fault": "storage", "storage_errno": errno.ENOSPC,
                   "exit_code": 1, "stdout": ""}
        self.assertEqual(case_errors("storage", storage, None, True), [])
        self.assertTrue(case_errors("storage", storage | {"exit_code": 0}, None, True))

    def test_oom_requires_new_host_kernel_events_after_native_startup(self):
        observed = {"controller_pid": 12, "fixture_pid": 15, "fixture_ready": True,
                    "fixture_stopped": True, "fixture_released": True, "fault": "oom"}
        before = {"limit_bytes": OOM_MEMORY, "swap_max_bytes": 0,
                  "events": {"max": 3, "oom": 2, "oom_kill": 1}}
        after = copy.deepcopy(before)
        after["events"] = {"max": 4, "oom": 3, "oom_kill": 2}
        memory = {"before": before, "after": after}
        self.assertEqual(case_errors("oom", observed, None, True, memory), [])
        for name in ("max", "oom", "oom_kill"):
            stale = copy.deepcopy(memory)
            stale["after"]["events"][name] = before["events"][name]
            with self.subTest(stale=name):
                self.assertTrue(case_errors("oom", observed, None, True, stale))
        for value in ({}, {"after": after}, memory | {"before": before | {"swap_max_bytes": OOM_MEMORY}},
                      memory | {"after": after | {"limit_bytes": OOM_MEMORY * 2}}):
            with self.subTest(memory=value):
                self.assertTrue(case_errors("oom", observed, None, True, value))
        for changes, cleanup in (({"fixture_ready": False}, True), ({"fixture_released": False}, True),
                                 ({"fixture_stopped": False}, True), ({}, False)):
            with self.subTest(changes=changes, cleanup=cleanup):
                self.assertTrue(case_errors("oom", observed | changes, None, cleanup, memory))
        self.assertTrue(case_errors("oom", observed,
                                    {"untrusted_inner_report": {"measurement": {"valid": True}}}, True, memory))

    def test_report_enospc_requires_midrun_failure_at_the_actual_destination(self):
        observed = {"controller_pid": 12, "fixture_pid": 15, "fixture_ready": True,
                    "fixture_stopped": True, "fixture_released": True, "fault": "report-storage",
                    "storage_errno": errno.ENOSPC, "destination_errno": errno.ENOSPC,
                    "destination_empty_before_fill": True, "destination_unchanged": True,
                    "destination_bytes": 0, "exit_code": 1, "stdout": PUBLICATION,
                    "stderr": "durante: cannot write final report\n"}
        self.assertEqual(case_errors("report-storage", observed, None, True), [])
        for changes in ({"fixture_ready": False}, {"fixture_released": False}, {"stdout": ""},
                        {"exit_code": 0}, {"storage_errno": errno.EACCES}, {"destination_errno": None},
                        {"destination_empty_before_fill": False}, {"destination_unchanged": False},
                        {"destination_bytes": 1}, {"stderr": "final report must be a fresh private file"}):
            with self.subTest(changes=changes):
                self.assertTrue(case_errors("report-storage", observed | changes, None, True))
        report = {"authority": "diagnostic_only", "integrity_verified": False}
        self.assertTrue(case_errors("report-storage", observed, report, True))

    def test_output_enospc_rejects_false_success_and_salvaged_measurements(self):
        observed = {"controller_pid": 12, "fixture_pid": 15, "fixture_ready": True,
                    "fixture_stopped": True, "fixture_released": True, "fault": "output-storage",
                    "storage_errno": errno.ENOSPC, "destination_errno": errno.ENOSPC,
                    "destination_empty_before_fill": True, "destination_unchanged": True,
                    "destination_bytes": 0, "exit_code": 1, "captured_stdout": ""}
        report = {"authority": "diagnostic_only", "integrity_verified": False,
                  "execution": {"state": "output_failed", "cleanup_complete": True,
                                "scratch_cleanup_complete": True},
                  "output": {"stdout_captured_bytes": len(PUBLICATION), "published": False, "complete": False}}
        self.assertEqual(case_errors("output-storage", observed, report, True), [])
        for key, changes in (("execution", {"state": "complete"}), ("execution", {"cleanup_complete": False}),
                             ("output", {"published": True}), ("output", {"stdout_captured_bytes": 0}),
                             ("output", {"complete": True}), ("measurement", {"instructions": 42})):
            invalid = copy.deepcopy(report)
            invalid.setdefault(key, {}).update(changes)
            with self.subTest(key=key, changes=changes):
                self.assertTrue(case_errors("output-storage", observed, invalid, True))
        self.assertTrue(case_errors("output-storage", observed, None, True))

    def test_captured_positive_control_requires_real_complete_output(self):
        observed = {"controller_pid": 12, "fixture_pid": 15, "fixture_ready": True,
                    "fixture_stopped": True, "fixture_released": True, "exit_code": 0,
                    "captured_stdout": PUBLICATION, "captured_stderr": ""}
        report = {"authority": "diagnostic_only", "integrity_verified": False,
                  "execution": {"state": "complete", "cleanup_complete": True,
                                "scratch_cleanup_complete": True},
                  "untrusted_inner_report": {"measurement": {"valid": True, "complete": True},
                                             "execution": {"exit_code": 0}},
                  "output": {"published": True, "complete": True}}
        self.assertEqual(case_errors("output-success", observed, report, True), [])
        self.assertTrue(case_errors("output-success", observed | {"captured_stdout": ""}, report, True))
        self.assertTrue(case_errors("output-success", observed, report | {"output": {"published": False}}, True))

    def test_both_positive_controls_reject_partial_measurements_and_nonzero_target_exit(self):
        observed = {"controller_pid": 12, "fixture_pid": 15, "fixture_ready": True,
                    "fixture_stopped": True, "fixture_released": True, "exit_code": 0,
                    "stdout": "owner-success\n", "captured_stdout": PUBLICATION, "captured_stderr": ""}
        report = {"authority": "diagnostic_only", "integrity_verified": False,
                  "execution": {"state": "complete", "cleanup_complete": True,
                                "scratch_cleanup_complete": True},
                  "untrusted_inner_report": {"measurement": {"valid": True, "complete": True},
                                             "execution": {"exit_code": 0}},
                  "output": {"published": True, "complete": True}}
        for case in ("success", "output-success"):
            for key, changes in (("measurement", {"complete": False}), ("execution", {"exit_code": 3}),
                                 ("execution", {"exit_code": None})):
                invalid = copy.deepcopy(report)
                invalid["untrusted_inner_report"][key].update(changes)
                with self.subTest(case=case, key=key, changes=changes):
                    self.assertTrue(case_errors(case, observed, invalid, True))

    def test_memory_snapshot_reads_kernel_events_and_swap_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            group = Path(directory)
            for name, value in {"memory.max": str(OOM_MEMORY), "memory.swap.max": "0",
                                "memory.current": "1024", "memory.events": "max 2\noom 1\noom_kill 1\n"}.items():
                (group / name).write_text(value)
            self.assertEqual(memory_snapshot(group), {"limit_bytes": OOM_MEMORY, "swap_max_bytes": 0,
                                                      "current_bytes": 1024,
                                                      "events": {"max": 2, "oom": 1, "oom_kill": 1}})
            (group / "memory.swap.max").unlink()
            with self.assertRaises(FileNotFoundError):
                memory_snapshot(group)  # Missing swap/event proof is not OOM coverage.

    def test_publication_barrier_requires_unique_actually_stopped_native_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            for pid, name, state in ((12, "durante", "T (stopped)"), (15, "owner-ready", "S (sleeping)")):
                (proc / str(pid)).mkdir()
                (proc / str(pid) / "status").write_text(f"Name:\t{name}\nState:\t{state}\n")
            self.assertIsNone(stopped_fixture(proc))
            (proc / "15/status").write_text("Name:\towner-ready\nState:\tT (stopped)\n")
            self.assertEqual(stopped_fixture(proc), 15)
            (proc / "12/status").write_text("Name:\towner-ready\nState:\tT (stopped)\n")
            with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                stopped_fixture(proc)

    def test_filler_enospc_alone_cannot_qualify_a_writable_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "report.json"
            destination.touch()
            with mock.patch("execution_owner.fill_private_storage", return_value={"storage_errno": errno.ENOSPC}):
                with self.assertRaisesRegex(RuntimeError, "still accepted"):
                    exhaust_destination(destination, {})

    def test_cgroup_observation_distinguishes_live_empty_and_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            group = Path(directory) / "group"
            group.mkdir()
            (group / "cgroup.events").write_text("populated 1\nfrozen 0\n")
            (group / "cgroup.procs").write_text("12\n15\n")
            self.assertEqual(cgroup_state(group),
                             {"exists": True, "populated": "1", "processes": [12, 15]})
            (group / "cgroup.events").write_text("populated 0\nfrozen 0\n")
            (group / "cgroup.procs").write_text("")
            self.assertEqual(cgroup_state(group),
                             {"exists": True, "populated": "0", "processes": []})
            (group / "cgroup.events").unlink()
            with self.assertRaises(FileNotFoundError):
                cgroup_state(group)  # Missing evidence is not an empty cgroup.
            (group / "cgroup.procs").unlink()
            group.rmdir()
            self.assertFalse(cgroup_state(group)["exists"])

    def test_cgroup_path_rejects_nonunified_and_traversing_paths(self):
        self.assertEqual(unified_cgroup("0::/system.slice/docker-owned.scope\n"),
                         "/system.slice/docker-owned.scope")
        for text in ("1:pids:/x\n", "0::/../x\n", "0::/x\n0::/y\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                unified_cgroup(text)

    def test_teardown_requires_owner_to_stop_a_live_group(self):
        def response(args, code=0, stdout=b"", stderr=b""):
            return subprocess.CompletedProcess(args, code, stdout, stderr)
        for running in (True, False):
            commands = []

            def docker(*args, **kwargs):
                commands.append(args)
                if args[0] == "inspect" and "--format" in args:
                    return response(args, stdout=b'{"Running":' + str(running).lower().encode() + b'}')
                if args[0] == "inspect":
                    return response(args, 1, stderr=b"error: no such object: owned")
                return response(args)

            states = [{"exists": True, "populated": "1" if running else "0",
                       "processes": [12] if running else []},
                      {"exists": False, "populated": "0", "processes": []}]
            with self.subTest(running=running), mock.patch("execution_owner.docker", side_effect=docker), \
                    mock.patch("execution_owner.cgroup_state", side_effect=states):
                result = stop_owned_container("owned", Path("/fixture/cgroup"))
            self.assertTrue(result["cleanup_complete"])
            self.assertEqual(result["live_before_owner_stop"], running)
            self.assertEqual(bool(result["errors"]), not running)
            self.assertIn(("rm", "--force", "owned"), commands)

    def test_cleanup_attempts_owned_name_after_inspection_timeout(self):
        commands = []

        def docker(*args, **kwargs):
            commands.append(args)
            if args[0] == "inspect" and "--format" in args:
                raise subprocess.TimeoutExpired("docker inspect", 15)
            return subprocess.CompletedProcess(args, 1, b"", b"No such object: owned-name")

        with mock.patch("execution_owner.docker", side_effect=docker):
            result = stop_owned_container("owned-name", None)
        self.assertIn(("rm", "--force", "owned-name"), commands)
        self.assertFalse(result["cleanup_complete"])
        self.assertTrue(result["errors"])


if __name__ == "__main__":
    unittest.main()

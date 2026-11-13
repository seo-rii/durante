import copy
import errno
import signal
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from execution_owner import (case_errors, cgroup_state, has_accepted_counts,
                             stop_owned_container, unified_cgroup)


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
                       {"untrusted_inner_report": {"measurement": []}}):
            with self.subTest(report=report):
                self.assertTrue(case_errors("controller-kill", observed, report, True))

    def test_positive_control_cannot_pass_on_unavailable_installation(self):
        observed = {"controller_pid": 12, "exit_code": 0, "stdout": "owner-success\n"}
        report = {"authority": "diagnostic_only", "integrity_verified": False,
                  "execution": {"state": "complete", "cleanup_complete": True,
                                "scratch_cleanup_complete": True},
                  "untrusted_inner_report": {"measurement": {"valid": True}}}
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

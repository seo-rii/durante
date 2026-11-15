"""Bounded owned-process diagnostics and their timeout/cleanup boundary."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import runtime_compat as runtime
from concurrent_spawn import own_subreaper


class ProcessSnapshotTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="durante-snapshot-unit-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    @staticmethod
    def stat(pid, parent, start):
        # Names can contain spaces, parentheses and non-ASCII bytes; drop them.
        fields = ["S", str(parent)] + ["0"] * 17 + [str(start)] + ["0"] * 3
        return f"{pid} (fixture (이름)) " + " ".join(fields) + "\n"

    def process(self, pid, parent=1, start=None, threads=None):
        start = start or pid * 10
        path = self.root / str(pid)
        path.mkdir()
        (path / "stat").write_text(self.stat(pid, parent, start))
        status = f"Name:\tfixture\nTgid:\t{pid}\nTracerPid:\t7\nCpus_allowed_list:\t0-1\n"
        (path / "status").write_text(status)
        (path / "wchan").write_text("futex_wait_queue")
        for tid, children in (threads or {pid: []}).items():
            task = path / "task" / str(tid)
            task.mkdir(parents=True)
            (task / "stat").write_text(self.stat(tid, parent, start + tid - pid))
            (task / "status").write_text(status)
            (task / "wchan").write_text("do_wait")
            (task / "children").write_text(" ".join(map(str, children)))
        return {"pid": pid, "starttime_ticks": start}

    def capture(self, identity, **kwargs):
        return runtime.process_snapshot(identity, proc_root=self.root, time_budget=1, **kwargs)

    def test_walks_nonleader_thread_children_without_scanning_other_processes(self):
        identity = self.process(10, threads={10: [], 11: [20]})
        self.process(20, parent=10, threads={20: [30]})
        self.process(30, parent=20)
        self.process(99)
        scanned = []
        original = os.scandir

        def scandir(path):
            scanned.append(Path(path))
            self.assertEqual(Path(path).name, "task")
            return original(path)

        with patch.object(runtime.os, "scandir", side_effect=scandir):
            result = self.capture(identity)
        self.assertTrue(result["complete"], result)
        self.assertTrue(result["root_revalidated"])
        self.assertEqual({row["pid"] for row in result["processes"]}, {10, 20, 30})
        self.assertEqual(len(scanned), 3)
        child = result["processes"][1]
        self.assertEqual(child["parent_identity"]["thread_id"], 11)
        self.assertEqual(child["parent_identity"]["starttime_ticks"], 100)
        self.assertEqual(child["tracer_pid"], 7)
        self.assertEqual(child["cpu_affinity_list"], "0-1")
        text = json.dumps(result)
        self.assertNotIn("fixture", text)
        self.assertNotIn("cmdline", text)
        self.assertNotIn("environ", text)

    def test_root_reuse_or_missing_launch_identity_stops_before_tree_walk(self):
        identity = self.process(10)
        for value in (None, {**identity, "starttime_ticks": 99}):
            with self.subTest(value=value), patch.object(runtime.os, "scandir", side_effect=AssertionError("must not walk")):
                result = self.capture(value)
            self.assertFalse(result["complete"])
            self.assertEqual(result["processes"], [])

    def test_reused_child_is_not_observed_under_its_old_identity(self):
        identity = self.process(10, threads={10: [20]})
        self.process(20, parent=10)
        original, calls = runtime._proc_stat, 0

        def changing(data, pid):
            nonlocal calls
            row = original(data, pid)
            if pid == 20:
                calls += 1
                if calls >= 2:
                    row["starttime_ticks"] += 1
            return row

        with patch.object(runtime, "_proc_stat", side_effect=changing):
            result = self.capture(identity)
        self.assertFalse(result["complete"])
        self.assertEqual([row["pid"] for row in result["processes"]], [10])

    def test_disappeared_or_reparented_child_is_partial(self):
        identity = self.process(10, threads={10: [20, 30]})
        self.process(30, parent=99)
        result = self.capture(identity)
        self.assertFalse(result["complete"])
        self.assertEqual([row["pid"] for row in result["processes"]], [10])
        self.assertTrue(any("unavailable" in row["reason"] for row in result["issues"]))
        self.assertTrue(any("parent changed" in row["reason"] for row in result["issues"]))

    def test_permission_and_oversize_files_remain_partial(self):
        identity = self.process(10)
        original = Path.open

        def deny(path, *args, **kwargs):
            if path.name == "status":
                raise PermissionError("fixture")
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", deny):
            denied = self.capture(identity)
        self.assertFalse(denied["complete"])
        self.assertIn("PermissionError", json.dumps(denied["issues"]))
        (self.root / "10/status").write_bytes(b"x" * 16385)
        oversized = self.capture(identity)
        self.assertFalse(oversized["complete"])
        self.assertLessEqual(oversized["bytes_read"], 262144)

    def test_process_thread_byte_and_time_caps_are_explicit(self):
        identity = self.process(10, threads={10: [20], 11: []})
        self.process(20, parent=10)
        for options, reason in (({"max_processes": 1}, "process limit"),
                                ({"max_threads": 1}, "thread limit"),
                                ({"max_total_threads": 1}, "total thread limit"),
                                ({"max_bytes": 64}, "read byte limit")):
            with self.subTest(options=options):
                result = self.capture(identity, **options)
                self.assertFalse(result["complete"])
                self.assertIn(reason, json.dumps(result["issues"]))
                self.assertLessEqual(result["bytes_read"], options.get("max_bytes", 262144))
        ticks = iter([0, 0.051, 0.051])
        timed = runtime.process_snapshot(identity, proc_root=self.root, clock=lambda: next(ticks))
        self.assertFalse(timed["complete"])
        self.assertIn("time limit", json.dumps(timed["issues"]))
        self.assertEqual(timed["bytes_read"], 0)
        self.assertEqual(timed["limits"]["seconds"], 0.05)

    def test_failed_thread_reads_consume_the_thread_budget(self):
        identity = self.process(10, threads={10: [], 11: [], 12: []})
        for path in (self.root / "10/task").iterdir():
            (path / "stat").unlink()
        result = self.capture(identity, max_threads=2)
        self.assertFalse(result["complete"])
        self.assertEqual(result["threads_inspected"], 2)
        self.assertEqual(result["threads_observed"], 0)
        self.assertIn("per-process thread limit", json.dumps(result["issues"]))

    def test_compact_output_limit_discards_excess_records_explicitly(self):
        identity = self.process(10)
        original = runtime._proc_stat

        def large_stat(data, pid):
            return {**original(data, pid), "state": "x" * 262144}

        with patch.object(runtime, "_proc_stat", side_effect=large_stat):
            result = self.capture(identity)
        self.assertFalse(result["complete"])
        self.assertEqual(result["processes"], [])
        self.assertIn("compact JSON byte limit", json.dumps(result["issues"]))
        self.assertLessEqual(len(json.dumps(result, separators=(",", ":")).encode()),
                             result["limits"]["compact_json_bytes"])


class RunProcessSnapshotTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="durante-timeout-unit-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def fake_process(self, *, kill_timeout=False):
        process = unittest.mock.Mock(pid=1234)
        waits = [subprocess.TimeoutExpired(["fixture"], 0.01)]
        if kill_timeout:
            waits.append(subprocess.TimeoutExpired(["fixture"], 2))
        process.wait.side_effect = waits + [0]
        process.poll.return_value = 0
        return process

    def test_snapshot_precedes_signal_and_does_not_change_timeout_failure(self):
        order = []
        with patch.object(runtime.subprocess, "Popen", return_value=self.fake_process()), \
             patch.object(runtime, "process_identity", return_value={"pid": 1234, "starttime_ticks": 9}), \
             patch.object(runtime, "process_snapshot", side_effect=lambda value: order.append("snapshot") or {"complete": False}), \
             patch.object(runtime.os, "killpg", side_effect=lambda *_: order.append("signal")), \
             patch.object(runtime, "cleanup_orphans", return_value=True) as cleanup:
            row = runtime.run_process(["fixture"], self.root, 10, "/fixture/prlimit")
        self.assertEqual(order, ["snapshot", "signal"])
        self.assertTrue(row["timeout"])
        self.assertFalse(row["infrastructure_ok"])
        self.assertIn("outer wall deadline expired", row["errors"])
        self.assertEqual(row["timeout_snapshot"], {"complete": False})
        cleanup.assert_called_once()

    def test_success_records_launch_affinity_without_collecting_tree(self):
        process = self.fake_process()
        process.wait.side_effect = [0]
        with patch.object(runtime.subprocess, "Popen", return_value=process), \
             patch.object(runtime, "process_identity", return_value={"pid": 1234, "starttime_ticks": 9}), \
             patch.object(runtime.os, "sched_getaffinity", return_value={2, 3}), \
             patch.object(runtime, "process_snapshot") as snapshot, \
             patch.object(runtime, "cleanup_orphans", return_value=True):
            row = runtime.run_process(["fixture"], self.root, 10, "/fixture/prlimit")
        snapshot.assert_not_called()
        self.assertFalse(row["timeout"])
        self.assertTrue(row["infrastructure_ok"])
        self.assertEqual(row["harness_cpu_affinity"], [2, 3])
        self.assertEqual(row["root_identity"]["starttime_ticks"], 9)

    def test_observer_failure_and_disappeared_group_still_publish_and_cleanup(self):
        for kill_timeout in (False, True):
            with self.subTest(kill_timeout=kill_timeout), \
                 patch.object(runtime.subprocess, "Popen", return_value=self.fake_process(kill_timeout=kill_timeout)), \
                 patch.object(runtime, "process_identity", side_effect=PermissionError), \
                 patch.object(runtime, "process_snapshot", side_effect=RuntimeError), \
                 patch.object(runtime.os, "killpg", side_effect=ProcessLookupError), \
                 patch.object(runtime, "cleanup_orphans", return_value=True) as cleanup:
                row = runtime.run_process(["fixture"], self.root, 10, "/fixture/prlimit")
            self.assertTrue(row["timeout"])
            self.assertFalse(row["infrastructure_ok"])
            self.assertTrue(row["cleanup_complete"])
            self.assertEqual(row["timeout_snapshot"]["error"], "RuntimeError")
            cleanup.assert_called_once()

    @unittest.skipUnless(sys.platform == "linux" and shutil.which("prlimit"), "requires Linux prlimit")
    def test_sleeping_parent_and_child_are_captured_then_reaped(self):
        # A nonleader thread creates the child; the parent remains asleep.
        code = ("import subprocess,sys,threading,time\n"
                "def child():\n"
                " p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\n"
                " print(p.pid,flush=True)\n"
                " p.wait()\n"
                "threading.Thread(target=child).start()\n"
                "time.sleep(30)\n")
        libc, previous = own_subreaper()
        started = time.monotonic()
        snapshot_function = runtime.process_snapshot
        try:
            # Exercise the real tree under a separate one-second observation
            # budget; deterministic-clock checks cover the default 50-ms cap.
            with patch.object(runtime, "process_snapshot", side_effect=lambda value: snapshot_function(value, time_budget=1)):
                row = runtime.run_process([sys.executable, "-I", "-S", "-c", code], self.root, 2000)
        finally:
            libc.prctl(36, previous, 0, 0, 0)
        self.assertTrue(row["timeout"], row)
        self.assertTrue(row["cleanup_complete"], row)
        self.assertFalse(row["infrastructure_ok"])
        snapshot = row["timeout_snapshot"]
        self.assertTrue(snapshot["complete"], snapshot)
        child = int(row["stdout"].strip())
        self.assertEqual({item["pid"] for item in snapshot["processes"]}, {row["root_pid"], child})
        child_record = next(item for item in snapshot["processes"] if item["pid"] == child)
        self.assertNotEqual(child_record["parent_identity"]["thread_id"], row["root_pid"])
        self.assertLess(time.monotonic() - started, 8)
        self.assertFalse(Path(f"/proc/{child}").exists())
        self.assertTrue(row["harness_cpu_affinity"])


if __name__ == "__main__":
    unittest.main()

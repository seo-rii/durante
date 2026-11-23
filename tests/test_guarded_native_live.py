import os
from fractions import Fraction
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from guarded_native import live
from guarded_native.anchor import CalibratedClock


@unittest.skipUnless(sys.platform == "linux" and hasattr(os, "pidfd_open"), "Linux pidfds")
class LiveCalibratedClock(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "clock.bin"
        self.clock = self.enterContext(self.path.open("x+b"))
        self.weights = {"instructions": Fraction(3), "rep_bytes": Fraction(1)}
        self.calibrated = CalibratedClock("", "", 1_000_000, self.weights, Fraction(9))
        self.result = {"polls": 0, "limit_ms": 1, "timeout_ms": 2000, "poll_ms": 1}

    def publish(self, instructions, movs, stos, invalid=0):
        os.pwrite(self.clock.fileno(), live.CLOCK.pack(1, invalid, instructions, movs, stos, 0), 0)

    def child(self, code):
        child = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
        def cleanup():
            if child.poll() is None:
                child.kill()
            child.wait()
        self.addCleanup(cleanup)
        return child

    def observe(self, child):
        return live.observe(child, self.clock, self.path, self.calibrated, self.result)

    def test_exact_policy_equality_and_large_counts(self):
        # 1 instruction + 2 MOVS bytes + 4 STOS bytes have exactly 9 weighted units.
        self.publish(1, 2, 4)
        child = self.child("pass")
        mode, words = self.observe(child)
        self.assertEqual((mode, child.returncode, self.calibrated.predict_ns(live.counts(words))),
                         ("terminal", 0, 1_000_000))
        n = 2**60
        c = CalibratedClock("", "", 3 * n, self.weights, Fraction(3 * n))
        self.assertEqual(c.predict_ns({"instructions": n, "rep_bytes": 1}), 3 * n + 1)

    def test_actual_kill_and_post_stop_invalidation(self):
        self.publish(1, 2, 5)
        child = self.child("import time; time.sleep(30)")
        mode, words = self.observe(child)
        self.assertEqual((mode, child.returncode, words[2:5]), ("live", -signal.SIGKILL, (1, 2, 5)))
        stop = live.stop
        def late_invalidation(process):
            code = stop(process)
            self.publish(1, 2, 5, invalid=1)
            return code
        with patch.object(live, "stop", side_effect=late_invalidation):
            with self.assertRaisesRegex(ValueError, "invalid or overflowing"):
                self.observe(self.child("import time; time.sleep(30)"))

    def test_between_poll_and_stop_natural_exit_uses_terminal(self):
        self.publish(9, 0, 0)
        child = self.child("import time; time.sleep(0.04)")
        with patch.object(live, "stop", side_effect=lambda proc: proc.wait()) as stop:
            mode, words = self.observe(child)
        stop.assert_called_once()
        self.assertEqual((mode, child.returncode, words[2]), ("terminal", 0, 9))

    def test_no_progress_uses_wall_watchdog_and_wrong_inode_fails(self):
        self.publish(0, 0, 0)
        self.result.update(timeout_ms=20, poll_ms=2000)
        child = self.child("import time; time.sleep(30)")
        self.assertEqual(self.observe(child), ("timed_out", None))
        self.assertEqual(child.returncode, -signal.SIGKILL)
        self.path.rename(self.path.with_suffix(".old"))
        self.path.write_bytes(live.CLOCK.pack(*live.ZERO))
        with self.assertRaisesRegex(ValueError, "live clock file changed"):
            live.snapshot(self.clock, self.path, live.ZERO)

    def test_decreasing_published_work_is_unavailable(self):
        self.publish(5, 1, 2)
        previous = live.snapshot(self.clock, self.path, live.ZERO)
        self.publish(6, 0, 2)
        with self.assertRaisesRegex(ValueError, "decreasing"):
            live.snapshot(self.clock, self.path, previous)

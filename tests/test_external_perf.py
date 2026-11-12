"""Policy tests only: synthetic observations never qualify the real PMU."""
import copy
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "external_perf", Path(__file__).resolve().parents[1] / "experiments" / "external_perf.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def observation(mode="control", size=200_000, repeat=0):
    return {"state": "observed", "stage": "complete", "cleanup_complete": True,
            "exit_code": 0, "probe_exit_code": 0, "root_pid": 123, "reaped": 1,
            "instructions": 100_000 + 4 * size, "time_enabled_ns": 500,
            "time_running_ns": 500, "mode": mode, "iterations": size, "repeat": repeat}


def matrix(repeats=2):
    return [observation(mode, size, repeat) for mode in probe.MODES
            for repeat in range(repeats) for size in probe.SIZES]


class ExternalCounterPolicyTests(unittest.TestCase):
    def test_complete_fixture_matrix(self):
        self.assertIsNone(probe.inspect_matrix(matrix(), 2))

    def test_unavailable_is_not_an_observation(self):
        for state in ("unavailable", "failed", "unsupported", "native_control", None):
            row = observation()
            row["state"] = state
            self.assertIsNotNone(probe.inspect_observation(row))

    def test_completion_and_reaping_required(self):
        for key, value in (("cleanup_complete", False), ("cleanup_complete", 1),
                           ("exit_code", 1), ("exit_code", False), ("reaped", 0),
                           ("root_pid", 0), ("probe_exit_code", 77),
                           ("probe_exit_code", False), ("stage", "counter_read")):
            with self.subTest(key=key, value=value):
                row = observation()
                row[key] = value
                self.assertIsNotNone(probe.inspect_observation(row))

    def test_missing_zero_and_invalid_counter_values(self):
        for key in ("instructions", "time_enabled_ns", "time_running_ns"):
            for value in (None, 0, -1, False, True, 1.0, "500", 1 << 64):
                with self.subTest(key=key, value=value):
                    row = observation()
                    row[key] = value
                    self.assertIsNotNone(probe.inspect_observation(row))

    def test_multiplexed_observation_is_never_scaled(self):
        for running in (1, 499, 501):
            row = observation()
            row["time_running_ns"] = running
            self.assertIsNotNone(probe.inspect_observation(row))

    def test_missing_or_duplicate_fixture_fails(self):
        rows = matrix()
        self.assertIsNotNone(probe.inspect_matrix(rows[:-1], 2))
        self.assertIsNotNone(probe.inspect_matrix(rows + [rows[0]], 2))
        rows[-1] = copy.deepcopy(rows[0])
        self.assertIsNotNone(probe.inspect_matrix(rows, 2))

    def test_unexpected_fixture_fails(self):
        rows = matrix()
        rows[0]["mode"] = "arbitrary_command"
        self.assertIsNotNone(probe.inspect_matrix(rows, 2))

    def test_every_mode_must_preserve_work_increment(self):
        # This catches suppression after prctl, fd access, fork, thread or exec,
        # even if the returned count and both time fields are otherwise positive.
        for mode in probe.MODES:
            with self.subTest(mode=mode):
                rows = matrix()
                for row in rows:
                    if row["mode"] == mode:
                        row["instructions"] = 100_000
                self.assertIsNotNone(probe.inspect_matrix(rows, 2))

    def test_each_repeat_must_qualify(self):
        rows = matrix()
        rows[-1]["instructions"] += probe.EXPECTED_DELTA
        self.assertIsNotNone(probe.inspect_matrix(rows, 2))

    def test_tolerance_does_not_accept_missing_half_of_work(self):
        rows = matrix()
        rows[-1]["instructions"] -= probe.EXPECTED_DELTA // 2
        self.assertIsNotNone(probe.inspect_matrix(rows, 2))


if __name__ == "__main__":
    unittest.main()

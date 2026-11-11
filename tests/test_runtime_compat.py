"""Regression checks for the known-unqualified JVM startup exception."""
import copy
import signal
from pathlib import Path
import unittest
from unittest.mock import patch

from runtime_compat import classify_case, inspect_accounting, telemetry_status


class RuntimeClassificationTests(unittest.TestCase):
    def startup_failure(self):
        controls = [{"passed": True, "infrastructure_ok": True, "cleanup_complete": True,
                     "diagnostic_integrity_failure": False} for _ in range(2)]
        failure = {"passed": False, "infrastructure_ok": True, "cleanup_complete": True,
                   "diagnostic_integrity_failure": False, "timeout": False,
                   "exit_code": -signal.SIGABRT, "measurement": {"valid": False},
                   "stdout": "A fatal error has been detected by the Java Runtime Environment\n"
                             "SIGSEGV (0xb)\nJRE version:  (17.0.21+9) (build )\n",
                   "stderr": ""}
        return controls + [copy.deepcopy(failure), copy.deepcopy(failure)]

    def test_documented_startup_failure_stays_unqualified(self):
        self.assertEqual(classify_case("java", self.startup_failure()), "known-unqualified")

    def test_successful_jvm_fixture_is_allowed_to_improve(self):
        rows = self.startup_failure()
        for row in rows:
            row["passed"] = True
        self.assertEqual(classify_case("java", rows), "passed")

    def test_other_runtime_cannot_borrow_jvm_exception(self):
        for runtime in ("cpython", "pypy", "node"):
            self.assertEqual(classify_case(runtime, self.startup_failure()), "regression")

    def test_exception_never_masks_regressions(self):
        edits = {
            "timeout": lambda rows: rows[2].update(timeout=True),
            "bad native": lambda rows: rows[0].update(passed=False),
            "bad engine control": lambda rows: rows[1].update(passed=False),
            "valid counters on crash": lambda rows: rows[2].update(measurement={"valid": True}),
            "counter integrity": lambda rows: rows[2].update(diagnostic_integrity_failure=True),
            "cleanup": lambda rows: rows[2].update(cleanup_complete=False),
            "unbounded inspection": lambda rows: rows[2].update(infrastructure_ok=False),
            "changed signature": lambda rows: rows[2].update(stdout="unrelated Java error"),
            "different JVM version": lambda rows: rows[2].update(
                stdout=rows[2]["stdout"].replace("(17.", "(21.")),
            "different signal": lambda rows: rows[2].update(exit_code=-signal.SIGKILL),
        }
        for name, edit in edits.items():
            with self.subTest(name=name):
                rows = self.startup_failure()
                edit(rows)
                self.assertEqual(classify_case("java", rows), "regression")


class RuntimeTelemetryTests(unittest.TestCase):
    def measurement(self, rep_enabled=True, unsupported=0):
        counters = {"instructions": 1000, "rep_bytes": 64 if rep_enabled else 0,
                    "rep_entries": 2 if rep_enabled else 0,
                    "unsupported_rep_entries": unsupported, "signals_observed": 0}
        return {"valid": True, "complete": True, "quality": "uninterrupted",
                "rep_bytes_enabled": rep_enabled, "process_count": 1, "image_count": 1,
                **counters, "processes": [counters.copy()], "segments": [counters.copy()]}

    def inspect(self, measurement, rep_enabled=True):
        with patch("runtime_compat.collector.collect", return_value=measurement):
            return inspect_accounting(measurement, Path("unused"), 1, 1, rep_enabled)

    def test_partial_optional_rep_still_qualifies_instruction_work(self):
        measurement = self.measurement(unsupported=343)
        self.assertEqual(self.inspect(measurement), ([], False))
        self.assertEqual(telemetry_status(measurement, True, True), "partial")
        self.assertEqual(telemetry_status(self.measurement(), True, True), "movs_stos_only")
        self.assertEqual(telemetry_status(self.measurement(False), False, True), "disabled")
        self.assertEqual(telemetry_status(measurement, True, False), "unavailable")

    def test_disabled_rep_cannot_hide_inconsistent_counters(self):
        for key in ("rep_bytes", "rep_entries", "unsupported_rep_entries"):
            with self.subTest(key=key):
                measurement = self.measurement(False)
                for counters in (measurement, *measurement["processes"], *measurement["segments"]):
                    counters[key] = 1
                errors, integrity = self.inspect(measurement, False)
                self.assertTrue(errors)
                self.assertTrue(integrity)
        self.assertEqual(self.inspect(self.measurement(False), False), ([], False))

    def test_invalid_counter_values_and_totals_remain_rejected(self):
        edits = {
            "zero work": lambda m: m.update(instructions=0),
            "float": lambda m: m.update(unsupported_rep_entries=1.5),
            "bool": lambda m: m.update(rep_entries=True),
            "negative": lambda m: m.update(rep_bytes=-1),
            "overflow": lambda m: m.update(unsupported_rep_entries=1 << 64),
            "segment overflow": lambda m: m["segments"][0].update(instructions=1 << 64),
            "process mismatch": lambda m: m["processes"][0].update(instructions=999),
            "missing entries": lambda m: m.update(rep_entries=0),
            "mode mismatch": lambda m: m.update(rep_bytes_enabled=False),
        }
        for name, edit in edits.items():
            with self.subTest(name=name):
                measurement = self.measurement(unsupported=3)
                edit(measurement)
                errors, integrity = self.inspect(measurement)
                self.assertTrue(errors)
                self.assertTrue(integrity)

    def test_invalid_reports_still_cannot_expose_partial_totals(self):
        self.assertEqual(self.inspect({"valid": False, "complete": False}),
                         (["measurement unavailable"], False))
        errors, integrity = self.inspect({"valid": False, "complete": False,
                                          "unsupported_rep_entries": 3})
        self.assertEqual(errors, ["invalid accounting exposed partial totals"])
        self.assertTrue(integrity)


if __name__ == "__main__":
    unittest.main()

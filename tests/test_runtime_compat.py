"""Regression checks for the known-unqualified JVM startup exception."""
import copy
import signal
import unittest

from runtime_compat import classify_case


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


if __name__ == "__main__":
    unittest.main()

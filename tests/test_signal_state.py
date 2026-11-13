"""A successful oracle cannot hide incomplete execution or promoted counters."""
import copy
import unittest

from signal_state import collector, expected_stdout, qualification_errors


class SignalQualificationTests(unittest.TestCase):
    def row(self):
        return {"exit_code": 0, "timeout": False, "cleanup_complete": True,
                "stdout": expected_stdout("fault", "sse2")}

    def measurement(self):
        return {"valid": True, "complete": True, "integrity_verified": False,
                "measurement_contract": collector.measurement_contract(),
                "quality": "signals_observed", "signals_observed": 2,
                "process_count": 1, "image_count": 1}

    def test_failed_execution_never_qualifies_even_with_expected_output(self):
        self.assertEqual(qualification_errors(self.row(), "fault", "sse2"), [])
        for changes in ({"exit_code": 72}, {"timeout": True}, {"cleanup_complete": False},
                        {"capture_error": "oversized"}, {"stdout": "{}\n"},
                        {"stderr": "unexpected runtime diagnostic"}):
            with self.subTest(changes=changes):
                self.assertTrue(qualification_errors({**self.row(), **changes}, "fault", "sse2"))

    def test_incomplete_or_promoted_measurement_never_qualifies(self):
        self.assertEqual(qualification_errors(self.row(), "fault", "sse2", self.measurement()), [])
        for changes in ({"valid": False}, {"complete": False}, {"integrity_verified": True},
                        {"measurement_contract": None}, {"quality": "uninterrupted"},
                        {"signals_observed": 1}, {"process_count": 2}, {"image_count": 2}):
            with self.subTest(changes=changes):
                report = {**self.measurement(), **changes}
                self.assertTrue(qualification_errors(self.row(), "fault", "sse2", report))
        report = copy.deepcopy(self.measurement())
        report["measurement_contract"]["trusted_verdict_eligible"] = True
        self.assertTrue(qualification_errors(self.row(), "fault", "sse2", report))


if __name__ == "__main__":
    unittest.main()

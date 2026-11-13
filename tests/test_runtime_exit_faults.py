"""An injected failure must be reached and must invalidate terminal accounting."""
import copy
import unittest

from runtime_exit_faults import observation_errors


class ExitFaultOracleTests(unittest.TestCase):
    def failure(self):
        return ({"errors": [], "infrastructure_ok": True, "exit_code": 40, "stdout": "",
                 "ready": True, "stderr": "<durante-test-exit-fault:context:cleaned=1>\n"
                     "<Application /test/app (123). Failed to synchronize with all threads when detaching.>\n"},
                {"valid": False, "complete": False}, [{"event": "start"}])

    def check(self, row, measurement, events):
        return observation_errors(row, measurement, events, "context", 1, "release")

    def test_injected_failure_after_completed_cleanup_rejects_terminal_records(self):
        row, measurement, events = self.failure()
        self.assertEqual(self.check(row, measurement, events), [])
        events.append({"event": "end"})
        self.assertIn("terminal record disagrees with exit failure", self.check(row, measurement, events))

    def test_no_records_wrong_exit_or_unreached_fault_cannot_pass(self):
        row, measurement, events = self.failure()
        self.assertTrue(self.check(row, measurement, []))
        for key, value in (("exit_code", 0), ("ready", False), ("infrastructure_ok", False),
                           ("stdout", "unexpected"), ("stderr", "")):
            changed = dict(row, **{key: value})
            self.assertTrue(self.check(changed, measurement, events), key)
        changed = dict(row, stderr=row["stderr"].replace("cleaned=1", "cleaned=0"))
        self.assertTrue(self.check(changed, measurement, events))

    def test_partial_or_valid_totals_on_failed_exit_are_rejected(self):
        row, measurement, events = self.failure()
        for key, value in (("valid", True), ("complete", True), ("instructions", 17),
                           ("rep_bytes", 0), ("processes", []), ("segments", [])):
            changed = dict(measurement, **{key: value})
            self.assertIn("failed exit exposed valid or partial accounting", self.check(row, changed, events))

    def test_debug_cache_message_does_not_hide_another_diagnostic(self):
        row, measurement, events = self.failure()
        row["stderr"] = "<Unknown processor cache size>\n" + row["stderr"]
        self.assertEqual(observation_errors(row, measurement, events, "context", 1, "debug"), [])
        self.assertTrue(self.check(row, measurement, events))
        row["stderr"] += "<unrelated failure>\n"
        self.assertTrue(observation_errors(row, measurement, events, "context", 1, "debug"))

    def test_normal_control_requires_a_complete_nonempty_measurement(self):
        row, _, events = self.failure()
        row.update(exit_code=0, stderr="")
        events.append({"event": "end"})
        measurement = {"valid": True, "complete": True, "instructions": 100,
                       "process_count": 1, "image_count": 1}
        self.assertEqual(observation_errors(row, measurement, events, "none", 0, "release"), [])
        for key in ("complete", "instructions", "process_count", "image_count"):
            changed = copy.deepcopy(measurement)
            del changed[key]
            self.assertTrue(observation_errors(row, changed, events, "none", 0, "release"))


if __name__ == "__main__":
    unittest.main()

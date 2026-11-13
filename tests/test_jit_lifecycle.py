"""Prevent replay overcounts from satisfying strict JIT qualification."""
import unittest
from pathlib import Path
import tempfile

from concurrent_spawn import collector
from jit_lifecycle import accounting_errors, accounting_status, expected_instructions, execution_errors, OUTPUT


class JitPolicyTests(unittest.TestCase):
    def test_ignored_engine_option_cannot_be_a_qualified_debug_run(self):
        row = {"exit_code": 0, "stdout": OUTPUT, "stderr": ""}
        self.assertEqual(execution_errors(row), [])
        row["stderr"] = "Option parsing error : unknown option -debug. Continuing\n"
        self.assertTrue(execution_errors(row))

    def test_reproduced_rwx_overcount_is_never_a_supported_result(self):
        observed = {"valid": True, "complete": True, "instructions": 172,
                    "rep_bytes": 18, "rep_entries": 3, "unsupported_rep_entries": 0,
                    "signals_observed": 0, "rep_telemetry_status": "movs_stos_only"}
        self.assertTrue(accounting_errors(observed, 169, 0, True))
        self.assertEqual(accounting_status(observed, 169, 0, True, "rwx-rewrite"), "regression")
        self.assertEqual(accounting_status(observed, 169, 0, True, "protect"), "regression")
        self.assertEqual(accounting_status({**observed, "instructions": 173}, 169, 0, True, "rwx-rewrite"), "regression")

    def test_missing_or_truncated_records_cannot_satisfy_qualification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for data in (None, b'{"event":"start"'):
                if data is not None:
                    (root / "process-1234.jsonl").write_bytes(data)
                measurement = collector.collect(root, 1234)
                self.assertEqual(accounting_status(measurement, 169, 0, True, "rwx-rewrite"), "regression")

    def test_corrected_rwx_counts_can_become_qualified(self):
        correct = {"valid": True, "complete": True, "instructions": 489,
                   "rep_bytes": 44, "rep_entries": 2, "unsupported_rep_entries": 0,
                   "signals_observed": 0}
        self.assertEqual(accounting_status(correct, 489, 32, True, "rwx-rewrite"), "qualified")
        for change in ({"valid": 1}, {"complete": False}, {"signals_observed": 1}):
            self.assertEqual(accounting_status({**correct, **change}, 489, 32, True, "rwx-rewrite"), "regression")
        self.assertEqual(accounting_status({**correct, "signals_observed": False},
                                          489, 32, True, "rwx-rewrite"), "regression")

    def test_exact_count_oracle_requires_the_complete_success_path(self):
        # Tool truncation must not quietly reduce the independent oracle.
        for text in ("", "0000401000 <_start>:\n  401000: ret\n",
                     "0000401001 <failure>:\n  401001: syscall\n"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    expected_instructions(text, 32)


if __name__ == "__main__":
    unittest.main()

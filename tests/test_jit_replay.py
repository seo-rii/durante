"""Strict independent-oracle and complete-matrix policies for replay fixtures."""
import unittest

from jit_replay import (EXECUTIONS, FIXTURES, OUTPUT, accounting_errors,
                        diagnostic_classification, execution_errors, expected_instructions,
                        matrix_errors)


def disassembly(loop=False):
    lines = ["0000000000401000 <_start>:"]
    lines += [f"  {0x401000 + i:x}: nop" for i in range(20)]
    if loop:
        lines += ["0000000000401014 <rewrite_loop>:"]
        lines += [f"  {0x401014 + i:x}: nop" for i in range(13)]
        lines += ["0000000000401021 <rewrite_loop_end>:"]
    lines += ["0000000000401100 <failure>:", "  401100: syscall"]
    return "\n".join(lines)


class JitReplayPolicyTests(unittest.TestCase):
    def test_quiet_success_remains_valid_in_every_execution_mode(self):
        for mode, core, _ in EXECUTIONS:
            with self.subTest(mode=mode, core=core):
                row = {"exit_code": 0, "stdout": OUTPUT, "stderr": "",
                       "execution_mode": mode, "core": core}
                self.assertEqual(execution_errors(row), [])
                self.assertEqual(diagnostic_classification(row), "none")

    def test_exact_cpu_cache_notice_is_classified_in_direct_debug_controls(self):
        # The retained CI failures had this exact line, exit zero and the fixed
        # replay output. Classification must retain the original diagnostic.
        notice = "<Unknown processor cache size>\n"
        for mode in ("engine", "counter-off", "counter-on"):
            with self.subTest(mode=mode):
                row = {"exit_code": 0, "stdout": OUTPUT, "stderr": notice,
                       "execution_mode": mode, "core": "debug"}
                self.assertEqual(execution_errors(row), [])
                self.assertEqual(diagnostic_classification(row), "cpu_cache_size_unknown")
                self.assertEqual(row["stderr"], notice)

    def test_cpu_cache_notice_is_not_accepted_outside_direct_debug_controls(self):
        for mode, core in (("native", None), ("native", "debug"),
                           ("supervisor", "release"), ("supervisor", "debug"),
                           ("engine", "release"), ("counter-off", "release"),
                           ("counter-on", "release"), ("engine", None),
                           ("unknown", "debug")):
            with self.subTest(mode=mode, core=core):
                row = {"exit_code": 0, "stdout": OUTPUT,
                       "stderr": "<Unknown processor cache size>\n",
                       "execution_mode": mode, "core": core}
                self.assertEqual(diagnostic_classification(row), "unexpected")
                self.assertIn("unexpected runtime or application diagnostic", execution_errors(row))

    def test_cache_notice_matching_does_not_strip_or_suppress_other_diagnostics(self):
        notice = "<Unknown processor cache size>\n"
        for stderr in (notice * 2, notice.rstrip("\n"), notice.replace("\n", "\r\n"),
                       " " + notice, notice + "\n", notice + "assertion failed\n",
                       "assertion failed\n" + notice, "<Unknown processor cache size!>\n",
                       "Option parsing error : unknown option -debug. Continuing\n",
                       notice + "Option parsing error : unknown option -debug. Continuing\n"):
            with self.subTest(stderr=stderr):
                row = {"exit_code": 0, "stdout": OUTPUT, "stderr": stderr,
                       "execution_mode": "counter-on", "core": "debug"}
                self.assertEqual(diagnostic_classification(row), "unexpected")
                self.assertIn("unexpected runtime or application diagnostic", execution_errors(row))
                self.assertEqual(row["stderr"], stderr)

    def test_classified_notice_cannot_hide_failed_exit_or_incorrect_output(self):
        row = {"exit_code": 0, "stdout": OUTPUT,
               "stderr": "<Unknown processor cache size>\n",
               "execution_mode": "counter-on", "core": "debug"}
        for change in ({"exit_code": 1}, {"exit_code": -11}, {"exit_code": None},
                       {"stdout": ""}, {"stdout": OUTPUT.rstrip("\n")},
                       {"stdout": "jit-lifecycle-ok\n"}, {"stdout": OUTPUT * 2}):
            with self.subTest(change=change):
                changed = {**row, **change}
                self.assertEqual(diagnostic_classification(changed), "cpu_cache_size_unknown")
                self.assertIn("fixture output or exit oracle failed", execution_errors(changed))

    def test_classified_notice_leaves_accounting_failures_strict(self):
        row = {"exit_code": 0, "stdout": OUTPUT,
               "stderr": "<Unknown processor cache size>\n",
               "execution_mode": "counter-on", "core": "debug"}
        self.assertEqual(execution_errors(row), [])
        measurement = {"valid": True, "complete": True, "instructions": 123,
                       "rep_bytes": 16, "rep_entries": 2,
                       "unsupported_rep_entries": 0, "signals_observed": 0}
        self.assertEqual(accounting_errors(measurement, 123, "rep-forward", True), [])
        for change in ({"instructions": 124}, {"rep_bytes": 12}, {"rep_entries": 3},
                       {"valid": 1}, {"complete": False}, {"signals_observed": 1}):
            with self.subTest(change=change):
                errors = execution_errors(row)
                errors.extend(accounting_errors({**measurement, **change}, 123, "rep-forward", True))
                self.assertTrue(errors)

    def test_independent_static_path_expands_fixed_generated_calls(self):
        for fixture in FIXTURES:
            with self.subTest(fixture=fixture):
                repeated = fixture == "rewrite-threshold"
                expected = (20 + 12 * 13 if repeated else 20) + FIXTURES[fixture]["generated_instructions"]
                self.assertEqual(expected_instructions(disassembly(repeated), fixture), expected)

    def test_truncated_disassembly_or_missing_loop_cannot_reduce_oracle(self):
        for text in ("", "0000000000401000 <_start>:\n  401000: nop",
                     disassembly().replace("<failure>", "<wrong_end>")):
            with self.assertRaises(ValueError):
                expected_instructions(text, "scalar-other")
        with self.assertRaises(ValueError):
            expected_instructions(disassembly(), "rewrite-threshold")
        with self.assertRaises(ValueError):
            expected_instructions(disassembly(True).replace("  401020: nop\n", ""), "rewrite-threshold")

    def test_every_replay_overcount_and_partial_request_is_a_failure(self):
        for fixture, oracle in FIXTURES.items():
            for enabled in (False, True):
                row = {"valid": True, "complete": True, "instructions": 123,
                       "rep_bytes": oracle["rep_bytes"] if enabled else 0,
                       "rep_entries": oracle["rep_entries"] if enabled else 0,
                       "unsupported_rep_entries": 0, "signals_observed": 0}
                self.assertEqual(accounting_errors(row, 123, fixture, enabled), [])
                for key, value in (("instructions", 126), ("instructions", 122),
                                   ("valid", 1), ("complete", False), ("signals_observed", 1),
                                   ("rep_entries", row["rep_entries"] + 1),
                                   ("rep_bytes", row["rep_bytes"] + 6)):
                    with self.subTest(fixture=fixture, rep=enabled, key=key):
                        self.assertTrue(accounting_errors({**row, key: value}, 123, fixture, enabled))
                if fixture.startswith("rep-") and enabled:
                    self.assertTrue(accounting_errors({**row, "rep_bytes": 12}, 123, fixture, enabled))
                self.assertTrue(accounting_errors({}, 123, fixture, enabled))

    def test_all_native_engine_and_counter_observations_are_required(self):
        rows = [{"fixture": fixture, "execution_mode": label, "core": core,
                 "case": fixture + label + str(core), "passed": True}
                for fixture in FIXTURES for label, core, _ in EXECUTIONS]
        self.assertEqual(len(rows), 48)
        self.assertEqual(matrix_errors(rows), [])
        self.assertTrue(matrix_errors(rows[:-1]))
        self.assertTrue(matrix_errors(rows + [rows[0]]))
        self.assertTrue(matrix_errors([{**rows[0], "passed": False}, *rows[1:]]))
        self.assertTrue(matrix_errors([{**rows[0], "execution_mode": "known-unqualified"}, *rows[1:]]))


if __name__ == "__main__":
    unittest.main()

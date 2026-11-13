"""Strict independent-oracle and complete-matrix policies for replay fixtures."""
import unittest

from jit_replay import (EXECUTIONS, FIXTURES, accounting_errors, expected_instructions,
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

"""The native qualification oracle must reject attribution errors at every level."""
import copy
import unittest

from jit_lifecycle import diagnostic_classification as jit_diagnostic_classification
from rep_breakdown import accounting_errors, diagnostic_classification, OUTPUT


def ordinary():
    fields = {"rep_bytes": 131, "rep_entries": 6, "unsupported_rep_entries": 0,
              "signals_observed": 0, "rep_breakdown": {
                  "version": 1, "movs_bytes": 65, "stos_bytes": 66,
                  "movs_entries": 3, "stos_entries": 3}}
    return {**copy.deepcopy(fields), "valid": True, "complete": True,
            "root_pid": 1, "process_count": 1, "image_count": 1,
            "processes": [{**copy.deepcopy(fields), "pid": 1, "images": 1}],
            "segments": [{**copy.deepcopy(fields), "pid": 1, "threads": 1}]}


class RepBreakdownOracleTests(unittest.TestCase):
    def test_successful_exec_preserves_two_known_debug_startup_notices(self):
        # Exact stderr from CI run 43685110737, job 132723846339. All three
        # controls exited 0 with the fixed output; both counter modes also
        # passed the unchanged per-image/process/tree REP oracle.
        notice = "<Unknown processor cache size>\n"
        for mode in ("engine", "counter-off", "counter-on"):
            row = {"fixture": "exec", "execution_mode": mode, "core": "debug",
                   "exit_code": 0, "stdout": OUTPUT, "stderr": notice * 2}
            before = copy.deepcopy(row)
            self.assertEqual(diagnostic_classification(row), "cpu_cache_size_unknown_after_exec")
            self.assertEqual(jit_diagnostic_classification(row), "unexpected")
            self.assertEqual(row, before)

    def test_exec_notice_does_not_expand_other_fixture_or_diagnostic_acceptance(self):
        notice = "<Unknown processor cache size>\n"
        row = {"fixture": "exec", "execution_mode": "counter-on", "core": "debug",
               "exit_code": 0, "stdout": OUTPUT, "stderr": notice * 2}
        for change in (
                *({"fixture": fixture} for fixture in ("ordinary", "fork", "vfork", "thread", "failed-exec")),
                {"core": "release"}, {"core": None}, {"execution_mode": "native"},
                {"execution_mode": "supervisor"}, {"stderr": notice * 3},
                {"stderr": notice * 2 + "assertion failed\n"},
                {"stderr": "Option parsing error\n" + notice * 2},
                {"stderr": (notice * 2).rstrip("\n")}, {"stderr": (notice * 2).replace("\n", "\r\n")}):
            with self.subTest(change=change):
                self.assertEqual(diagnostic_classification({**row, **change}), "unexpected")
        for fixture in ("ordinary", "fork", "exec", "failed-exec", "thread", "vfork"):
            for stderr, expected in (("", "none"), (notice, "cpu_cache_size_unknown")):
                self.assertEqual(diagnostic_classification({**row, "fixture": fixture, "stderr": stderr}), expected)

    def test_operation_swap_cannot_hide_behind_unchanged_totals(self):
        self.assertEqual(accounting_errors(ordinary(), "ordinary", True), [])
        for level in ("total", "processes", "segments"):
            measurement = ordinary()
            row = measurement if level == "total" else measurement[level][0]
            row["rep_breakdown"]["movs_bytes"] = 66
            row["rep_breakdown"]["stos_bytes"] = 65
            self.assertTrue(accounting_errors(measurement, "ordinary", True), level)

    def test_zero_work_requires_an_explicit_measured_breakdown(self):
        measurement = ordinary()
        for row in [measurement, *measurement["processes"], *measurement["segments"]]:
            row["rep_bytes"] = row["rep_entries"] = 0
            row["rep_breakdown"] = {"version": 1, "movs_bytes": 0, "stos_bytes": 0,
                                    "movs_entries": 0, "stos_entries": 0}
        self.assertEqual(accounting_errors(measurement, "ordinary", False), [])
        for value in (None, {}, {"version": True, "movs_bytes": 0, "stos_bytes": 0,
                                "movs_entries": 0, "stos_entries": 0}):
            changed = copy.deepcopy(measurement)
            changed["rep_breakdown"] = value
            self.assertTrue(accounting_errors(changed, "ordinary", False))


if __name__ == "__main__":
    unittest.main()

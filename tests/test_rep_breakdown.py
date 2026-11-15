"""The native qualification oracle must reject attribution errors at every level."""
import copy
import unittest

from rep_breakdown import accounting_errors


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

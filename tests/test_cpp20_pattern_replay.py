from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from cpp20_pattern_replay import compare, read_pattern


class PatternReplayTests(unittest.TestCase):
    def test_median_and_extreme_bounds_use_independent_native_and_counter_ranges(self):
        search = {"native_cpu_ns": [90, 100, 100, 100, 100, 110],
                  "observations": [{"m": 25, "zero": 0}, {"m": 26, "zero": 0}, {"m": 27, "zero": 0}]}
        window = {"native_cpu_ns": [10, 20, 20, 20, 20, 30],
                  "observations": [{"m": 10, "zero": 0}, {"m": 10, "zero": 0}, {"m": 10, "zero": 0}]}
        result = compare(search, window)
        self.assertEqual(result["required_search_to_window"], {"median": "10/3", "fastest_search_slowest_window": "2"})
        self.assertEqual(result["features"]["m"]["median"], "insufficient")
        self.assertEqual(result["features"]["m"]["fastest_search_slowest_window"], "spans")
        self.assertEqual(result["features"]["zero"]["median"], "insufficient")
        window["observations"][2]["m"] = 15
        self.assertEqual(compare(search, window)["features"]["m"]["fastest_search_slowest_window"], "overlap")
        search["observations"] = [{"m": 30}]
        window["observations"] = [{"m": 15}]
        self.assertEqual(compare(search, window)["features"]["m"]["fastest_search_slowest_window"], "spans")

    def test_same_process_receipt_and_integrity_contract(self):
        raw = {"instructions": 100, "loads": 20, "bpred": True, "cbr_executed": 8, "cond_branches": 8,
               "mispredicts": 2, "load_pattern": {"version": 1, "first": 1, "same_line": 1,
                                                "adjacent_line": 2, "nonadjacent_line": 3}}
        meter = {"instructions": 100, "rep_bytes": 64, "latency_bound": {"cycles_x16": 400},
                 "authority": "diagnostic_only", "integrity_verified": False,
                 "measurement_contract": {"counter_integrity": "application_mutable", "trusted_verdict_eligible": False}}
        self.assertEqual(read_pattern(raw, meter)["nonadjacent_line"], 3)
        with self.assertRaisesRegex(ValueError, "instruction totals"):
            read_pattern({**raw, "instructions": 99}, meter)
        with self.assertRaisesRegex(ValueError, "counter contract"):
            read_pattern(raw, {**meter, "integrity_verified": True})


if __name__ == "__main__":
    unittest.main()

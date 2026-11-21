from collections import Counter
import importlib.util
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dependency_locality", ROOT / "experiments/dependency_pairs/followup/locality.py")
locality = importlib.util.module_from_spec(spec)
spec.loader.exec_module(locality)


class SuccessorFields(unittest.TestCase):
    def test_both_orders_and_both_lane_counts_visit_each_record_equally(self):
        size, laps = 64, 33
        with patch.object(locality, "ENTRIES", size), patch.object(locality, "OPERATIONS", size*laps):
            permutation = [locality.position(True, i) for i in range(size)]
            self.assertEqual(set(permutation), set(range(size)))
            self.assertNotEqual(permutation, list(range(size)))
            table = [[(i+1) % size, None] for i in range(size)]
            for i, index in enumerate(permutation):
                table[index][1] = permutation[(i+1) % size]
            for scattered in (False, True):
                for lanes in (1, 4):
                    with self.subTest(scattered=scattered, lanes=lanes):
                        self.check_walk(table, laps, scattered, lanes)

    def check_walk(self, table, laps, scattered, lanes):
        quarter = len(table)*laps//4
        positions = [locality.position(scattered, i*quarter) for i in range(4)]
        visits = Counter()
        value = 0x21a93f5c7de10648
        for _ in range(4):
            for _ in range(quarter//lanes):
                for i in range(lanes):
                    visits[positions[i]] += 1
                    positions[i] = table[positions[i]][int(scattered)]
            value = locality.base["rotate"](value ^ positions[0])
        for i in (1, 2, 3):
            value ^= positions[i]
            if i < 3:
                value = locality.base["rotate"](value)
        self.assertEqual(visits, {i: laps for i in range(len(table))})
        self.assertEqual(struct.pack("<Q", value), locality.checksum(scattered, lanes))


if __name__ == "__main__":
    unittest.main()

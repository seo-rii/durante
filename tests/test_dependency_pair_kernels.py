import importlib.util
from collections import Counter
from pathlib import Path
import struct
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "experiments/dependency_pairs/fixtures.py"
SPEC = importlib.util.spec_from_file_location("dependency_kernel_fixtures", SOURCE)
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)


class DependencyKernelTests(unittest.TestCase):
    def test_four_lanes_partition_the_serial_stream_and_match_checkpoints(self):
        for params in ({"entries": 1024, "operations": 3088},
                       {"addend": 257, "operations": 3088},
                       {"addend": (1 << 30) // 251 * 251 + 257, "operations": 3088}):
            if "entries" in params:
                size = params["entries"]
                order = [fixtures.position(params, i) for i in range(size)]
                self.assertEqual(set(order), set(range(size)))
                after = dict(zip(order, order[1:] + order[:1], strict=True))
                advance = after.__getitem__
            else:
                def advance(state):
                    return (state + params["addend"]) % 251

            inventories = []
            for lanes in (1, 4):
                states = [fixtures.position(params, i * params["operations"] // 4) for i in range(4)]
                inventory = Counter()
                checksum = 0x21a93f5c7de10648
                for _ in range(4):
                    for _ in range(params["operations"] // 16):
                        for lane in range(4):
                            target = lane % lanes
                            inventory[states[target]] += 1
                            states[target] = advance(states[target])
                    checksum = fixtures.rotate(checksum ^ states[0])
                checksum = fixtures.rotate(checksum ^ states[1])
                checksum = fixtures.rotate(checksum ^ states[2]) ^ states[3]
                with self.subTest(params=params, lanes=lanes):
                    self.assertEqual(struct.pack("<Q", checksum), fixtures.oracle(params, lanes))
                    self.assertEqual(sum(inventory.values()), params["operations"])
                inventories.append(inventory)
            self.assertEqual(inventories[0], inventories[1])

    def test_width_changes_dividend_magnitude_and_preserves_remainders(self):
        small = fixtures.PAIRS["div_small"]["addend"]
        large = fixtures.PAIRS["div_large"]["addend"]
        self.assertEqual(small % 251, large % 251)
        for state in range(251):
            self.assertEqual((small + state).bit_length(), 9)
            self.assertEqual((large + state).bit_length(), 31)


if __name__ == "__main__":
    unittest.main()

"""Check the IOI Wall orders, point-update oracle and balanced tree traversal."""
from collections import Counter
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from contest_branch import corpus


class WallBranchCases(unittest.TestCase):
    def test_orders_have_the_same_updates_and_path_visits(self):
        size = 8192
        orders = {}
        for name in corpus.NAMES:
            text, expected = corpus.make(name, size)
            header, *commands = text.splitlines()
            self.assertEqual(header, f"{size} {size}")
            wall = [0] * size
            positions = []
            visits = Counter()
            for line in commands:
                kind, left, right, height = map(int, line.split())
                self.assertEqual((kind, right), (1, left))
                self.assertTrue(0 <= left < size)
                self.assertTrue(0 < height <= 100_000)
                wall[left] = max(wall[left], height)
                positions.append(left)
                node = size + left
                while node:
                    visits[node] += 1
                    node //= 2
            self.assertEqual(sorted(positions), list(range(size)))
            self.assertEqual(wall, list(map(int, expected.splitlines())))
            if name == "ascending":
                base_visits, base_heights = visits, wall
            else:
                self.assertEqual(visits, base_visits)
                self.assertEqual(wall, base_heights)
            orders[name] = positions
        self.assertEqual(len({tuple(order) for order in orders.values()}), len(corpus.NAMES))
        self.assertEqual(orders["ascending"], list(range(size)))
        for name, width in (("tiles32", 32), ("tiles4096", 4096)):
            positions = orders[name]
            for i in range(0, size, width):
                self.assertEqual(sorted(positions[i:i + width]), list(range(i, i + width)))
        self.assertEqual(orders["bit_reversed"][:8], [0, 4096, 2048, 6144, 1024, 5120, 3072, 7168])

    def test_unknown_order_and_nonbalanced_or_out_of_bounds_sizes(self):
        with self.assertRaises(ValueError):
            corpus.make("unknown", 8)
        for size in (0, 7, 1 << 19):
            with self.assertRaises(ValueError):
                corpus.make("ascending", size)


if __name__ == "__main__":
    unittest.main()

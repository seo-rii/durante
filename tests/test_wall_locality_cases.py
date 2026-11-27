"""Check range permutations, the underlying Wall operations and their oracle."""
from collections import Counter
from hashlib import sha256
from itertools import groupby
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from wall_locality import corpus


class WallRangeCases(unittest.TestCase):
    def test_orders_preserve_overlapping_operations_and_final_wall(self):
        size = 1 << 14
        count = size - 7
        orders = {}
        for name in corpus.NAMES:
            text, expected = corpus.make(name, size)
            header, *lines = text.splitlines()
            self.assertEqual(header, f"{size} {count}")
            self.assertEqual(len(lines), count)
            commands = [tuple(map(int, line.split())) for line in lines]
            starts = [left for _, left, _, _ in commands]
            orders[name] = starts
            if name == "ascending":
                ordered_commands = commands
                self.assertEqual(starts, list(range(count)))
            else:
                self.assertEqual(sorted(commands), ordered_commands)
            wall = [0] * size
            for kind, left, right, height in commands:
                self.assertEqual((kind, right), (1, left + 7))
                self.assertTrue(0 < height <= 100_000)
                for pos in range(left, right + 1):
                    wall[pos] = max(wall[pos], height)
            self.assertEqual(list(map(int, expected.splitlines())), wall)

        self.assertEqual(len({tuple(order) for order in orders.values()}), len(orders))
        for name, width in (("tiles128", 128), ("tiles8192", 8192)):
            for start in range(0, count, width):
                block = orders[name][start:start + width]
                self.assertEqual(sorted(block), list(range(start, start + len(block))))
        for name, stride in (("stride64", 64), ("page_round_robin", 512)):
            residues = groupby(orders[name], key=lambda left: left % stride)
            self.assertEqual([list(values) for _, values in residues],
                             [list(range(offset, count, stride)) for offset in range(stride)])
        blocks = groupby(orders["shuffled_tiles"], key=lambda left: left // 256)
        for tile, values in blocks:
            self.assertEqual(list(values), list(range(tile * 256, min((tile + 1) * 256, count))))

    def test_each_order_visits_the_same_multiset_of_tree_nodes(self):
        size = 1024
        def visit(node, start, end, left, right, counts):
            counts[node] += 1
            if left <= start and end <= right:
                return
            mid = (start + end) // 2
            if left <= mid:
                visit(2 * node, start, mid, left, right, counts)
            if mid < right:
                visit(2 * node + 1, mid + 1, end, left, right, counts)

        for name in corpus.NAMES:
            counts = Counter()
            text, _ = corpus.make(name, size)
            for line in text.splitlines()[1:]:
                _, left, right, _ = map(int, line.split())
                visit(1, 0, size - 1, left, right, counts)
            if name == "ascending":
                reference = counts
            else:
                self.assertEqual(counts, reference)

    def test_registered_full_inputs_and_independent_output_hash(self):
        plan = json.loads(Path(corpus.__file__).with_name("plan.json").read_bytes())
        wall = [0] * corpus.SIZE
        for left in range(corpus.SIZE - 7):
            height = 10_000 + (left * 35_617 + 7_921) % 90_000
            for pos in range(left, left + 8):
                wall[pos] = max(wall[pos], height)
        output_hash = sha256("".join(f"{height}\n" for height in wall).encode()).hexdigest()
        self.assertEqual([r["name"] for r in plan["cases"]], list(corpus.NAMES))
        for row in plan["cases"]:
            text, output = corpus.make(row["name"])
            self.assertEqual(sha256(text.encode()).hexdigest(), row["stdin_sha256"])
            self.assertEqual(sha256(output.encode()).hexdigest(), output_hash)
            self.assertEqual(output_hash, row["stdout_sha256"])

    def test_oracle_handles_first_and_last_cells_and_single_update(self):
        for name in corpus.NAMES:
            text, expected = corpus.make(name, 8)
            self.assertEqual(text, "8 1\n1 0 7 17921\n")
            self.assertEqual(expected, "17921\n" * 8)
        for size in (7, 500_008):
            with self.assertRaises(ValueError):
                corpus.make("ascending", size)
        with self.assertRaises(ValueError):
            corpus.make("unknown")

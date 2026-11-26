"""Validate the shared Scrivener test inputs and their new orderings."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sys
import unittest

EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXPERIMENTS))
from scrivener_cache import corpus
from scrivener_order.corpus import count_queries


class ScrivenerCacheCases(unittest.TestCase):
    def test_old_endpoints_reproduce_the_registered_bytes(self):
        registered = json.loads((EXPERIMENTS / "scrivener_order/plan.json").read_bytes())["cases"]
        by_name = {case["name"]: case for case in registered}
        for name in ("ascending", "shuffled"):
            text, output = corpus.make(name)
            self.assertEqual(sha256(text.encode()).hexdigest(), by_name[name]["stdin_sha256"])
            self.assertEqual(sha256(output.encode()).hexdigest(), by_name[name]["stdout_sha256"])

    def test_same_positions_and_answers_with_new_permutation_structure(self):
        size = 2 * 4096 + 17
        orders = {}
        stats = []
        prefixes = set()
        for name in corpus.NAMES:
            text, answer = corpus.make(name, size)
            header, *commands = text.splitlines()
            self.assertEqual(int(header), len(commands))
            self.assertEqual(len(commands), 2 * size)
            self.assertTrue(all(cmd.startswith("T ") for cmd in commands[:size]))
            self.assertTrue(all(cmd.startswith("P ") for cmd in commands[size:]))
            letters = "".join(cmd.split()[1] for cmd in commands[:size])
            positions = [int(cmd.split()[1]) for cmd in commands[size:]]
            self.assertEqual(answer.splitlines(), [letters[i] for i in positions])
            prefixes.add(letters)
            orders[name] = positions
            stats.append(count_queries(text))
        self.assertEqual(len(prefixes), 1)
        self.assertEqual(len({tuple(positions) for positions in orders.values()}), len(corpus.NAMES))
        asc = orders["ascending"]
        for positions, case_stats in zip(orders.values(), stats, strict=True):
            self.assertEqual(Counter(positions), Counter(asc))
            self.assertEqual(case_stats, stats[0])
        for name, width in (("tiles32", 32), ("tiles4096", 4096)):
            positions = orders[name]
            for i in range(0, size, width):
                self.assertEqual(sorted(positions[i:i + width]), asc[i:i + width])
            self.assertNotEqual(positions[:width], asc[:width])
        expected = []
        for start in reversed(range(0, size, 256)):
            expected.extend(asc[start:start + 256])
        self.assertEqual(orders["reverse_tiles"], expected)

    def test_order_statistic_uses_queries_only(self):
        text = "7\nT a\nP 0\nT b\nP 0\nP 1\nT c\nP 2\n"
        self.assertEqual(corpus.position_bits(text), 2)
        self.assertEqual(corpus.position_bits("3\nT x\nT y\nP 1\n"), 0)
        self.assertEqual(corpus.position_bits("4\nP 9\nP 2\nP 2\nP 10\n"), 7)


if __name__ == "__main__":
    unittest.main()

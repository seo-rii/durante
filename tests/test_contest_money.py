"""Contest-format solver and independent generators for the 2017 World Finals market problem."""
from pathlib import Path
from random import Random
import subprocess
import sys
import tempfile
import unittest

EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXPERIMENTS))
from contest_accuracy import money


def brute(producers, consumers):
    return max((max(0, q - p) * max(0, e - d) for p, d in producers for q, e in consumers), default=0)


class MoneyForNothing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.binary = Path(directory.name) / "money"
        subprocess.run(["g++", "-std=c++20", "-O2", EXPERIMENTS / "contest_accuracy/money.cpp",
                        "-o", cls.binary], check=True)

    def check(self, producers, consumers):
        lines = [f"{len(producers)} {len(consumers)}"]
        lines.extend(f"{p} {d}" for p, d in producers + consumers)
        process = subprocess.run([self.binary], input="\n".join(lines) + "\n", text=True,
                                 capture_output=True, check=True)
        self.assertEqual(process.stderr, "")
        self.assertEqual(process.stdout, f"{brute(producers, consumers)}\n")

    def test_empty_intervals_duplicates_ties_and_wide_products(self):
        self.check([(20, 20)], [(19, 19), (21, 20), (20, 21)])
        self.check([(5, 9), (5, 7), (8, 7), (5, 7)], [(8, 10), (6, 16), (8, 9), (6, 16)])
        self.check([(1, 1), (1_000_000_000, 1)], [(1_000_000_000, 1_000_000_000)])
        self.check([(1, 30), (10, 15), (20, 3)], [(5, 39), (15, 10), (25, 8)])

    def test_differential_arbitrary_small_markets(self):
        rng = Random(19203)
        for _ in range(64):
            def offers():
                return [(rng.randint(1, 40), rng.randint(1, 40)) for _ in range(rng.randint(1, 22))]
            self.check(offers(), offers())

    def test_generator_oracle_against_cartesian_product(self):
        for case in money.CASES:
            with self.subTest(case=case):
                producers, consumers, expected = money.market(case, 193)
                self.assertEqual(expected, brute(producers, consumers))
                self.check(producers, consumers)


if __name__ == "__main__":
    unittest.main()

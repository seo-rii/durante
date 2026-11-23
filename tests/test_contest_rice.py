"""Check Rice Hub against exhaustive choices of hub and nearest fields."""
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from contest_accuracy import rice


class RiceHub(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.binary = Path(temporary.name) / "rice"
        subprocess.run(["g++", "-std=c++20", "-O2", ROOT / "experiments/contest_accuracy/rice.cpp",
                        "-o", cls.binary], check=True)

    def solve(self, text):
        return subprocess.run([self.binary], input=text, capture_output=True, text=True, check=True).stdout

    def test_all_hubs_on_small_roads(self):
        rng = random.Random(1494)
        for _ in range(36):
            length = rng.randrange(1, 25)
            fields = sorted(rng.randint(1, length) for _ in range(rng.randint(1, 12)))
            budget = rng.choice((0, 1, 2, 10, 23, rng.randrange(90)))
            expected = 0
            for hub in range(1, length + 1):
                cost = 0
                for count, distance in enumerate(sorted(abs(x - hub) for x in fields), 1):
                    cost += distance
                    if cost <= budget:
                        expected = max(count, expected)
            with self.subTest(fields=fields, budget=budget):
                self.assertEqual(rice.best_window(fields, budget), expected)
                data = f"{len(fields)} {length} {budget}\n" + "".join(f"{x}\n" for x in fields)
                self.assertEqual(self.solve(data), f"{expected}\n")

    def test_uses_64bit_budget_and_duplicate_positions(self):
        fields = [1] * 4 + [1_000_000_000] * 4
        for budget, expected in ((0, 4), (2_999_999_996, 6), (2_999_999_997, 7), (2_000_000_000_000_000, 8)):
            text = f"8 1000000000 {budget}\n" + "".join(f"{field}\n" for field in fields)
            with self.subTest(budget=budget):
                self.assertEqual(self.solve(text), f"{expected}\n")

    def test_registered_coordinates_and_answers(self):
        for name in rice.CASES:
            with self.subTest(name=name):
                text, expected = rice.make(name)
                header, *rest = text.splitlines()
                count, length, budget = map(int, header.split())
                fields = list(map(int, rest))
                self.assertEqual(count, len(fields))
                self.assertTrue(1 <= count <= 100_000 and 1 <= fields[0] <= fields[-1] <= length <= 10**9)
                self.assertTrue(0 <= budget <= 2 * 10**15)
                self.assertEqual(fields, sorted(fields))
                self.assertEqual(self.solve(text), expected)


if __name__ == "__main__":
    unittest.main()

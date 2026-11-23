"""Compare both Rice Hub submissions to independent optima on the same stdin."""
import itertools
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from contest_accuracy import rice


def exhaustive(fields, budget):
    for count in range(len(fields), 0, -1):
        for selected in itertools.combinations(fields, count):
            hub = selected[count // 2]
            if sum(abs(position - hub) for position in selected) <= budget:
                return count


class RiceSubmissions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.binaries = []
        for source in ("contest_accuracy/rice.cpp", "contest_submissions/rice_window.cpp"):
            path = ROOT / "experiments" / source
            binary = Path(temporary.name) / path.stem
            subprocess.run(["g++", "-std=c++20", "-O2", path, "-o", binary], check=True)
            cls.binaries.append(binary)

    def check(self, text, expected):
        for binary in self.binaries:
            with self.subTest(binary=binary.name):
                run = subprocess.run([binary], input=text, capture_output=True, text=True, check=True)
                self.assertEqual(run.stdout, expected)

    def test_small_subsets(self):
        rng = random.Random(60765)
        for _ in range(45):
            fields = sorted(rng.choices(range(1, 21), k=rng.randint(1, 9)))
            budget = rng.choice([0, 1, 8, rng.randrange(40)])
            with self.subTest(fields=fields, budget=budget):
                text = f"{len(fields)} 20 {budget}\n" + "\n".join(map(str, fields)) + "\n"
                self.check(text, f"{exhaustive(fields, budget)}\n")

    def test_wide_coordinates_at_exact_budget(self):
        half = 50_000
        gap = 999_999_999
        positions = "1\n" * half + "1000000000\n" * half
        cost = half * gap
        for budget, expected in ((0, half), (cost - 1, 2 * half - 1),
                                 (cost, 2 * half), (2 * 10**15, 2 * half)):
            with self.subTest(budget=budget):
                self.check(f"{2 * half} 1000000000 {budget}\n" + positions, f"{expected}\n")

    def test_registered_inputs(self):
        for name in rice.CASES:
            with self.subTest(name=name):
                text, expected = rice.make(name)
                self.check(text, expected)


if __name__ == "__main__":
    unittest.main()

"""Check the Wall text-grader executable against independent scalar updates."""
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from contest_accuracy import wall


class ContestWallTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.binary = Path(directory.name) / "wall"
        subprocess.run(["g++", "-std=c++20", "-O2", ROOT / "experiments/contest_accuracy/wall.cpp",
                        "-o", cls.binary], check=True)

    def check(self, stdin, stdout):
        result = subprocess.run([self.binary], input=stdin, text=True, capture_output=True, check=True)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, stdout)

    def test_boundaries_and_operation_order(self):
        self.check("1 5\n2 0 0 0\n1 0 0 100000\n1 0 0 67\n2 0 0 3\n1 0 0 17\n", "17\n")
        self.check("5 4\n1 1 3 100000\n2 2 4 0\n1 0 2 8\n2 0 0 7\n", "7\n100000\n8\n0\n0\n")

    def test_differential_inclusive_ranges(self):
        rng = random.Random(141413)
        for n in (1, 2, 3, 9, 64, 131):
            expected = [0] * n
            lines = [f"{n} 200"]
            for _ in range(200):
                op = rng.choice((1, 2))
                left, right = sorted((rng.randrange(n), rng.randrange(n)))
                h = rng.randint(0, 100000)
                lines.append(f"{op} {left} {right} {h}")
                f = max if op == 1 else min
                for pos in range(left, right + 1):
                    expected[pos] = f(expected[pos], h)
            with self.subTest(n=n):
                self.check("\n".join(lines) + "\n", "\n".join(map(str, expected)) + "\n")

    def test_registered_distributions(self):
        for name in wall.CASES:
            with self.subTest(name=name):
                stdin, stdout = wall.make(name)
                self.check(stdin, stdout)

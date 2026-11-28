import itertools
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import inputs

HERE = Path(__file__).resolve().parent


def quadratic(values):
    pairs = sum(left > right for i, left in enumerate(values) for right in values[i + 1:])
    return f"{pairs}\n".encode("ascii")


class InversionInputs(unittest.TestCase):
    def test_oracle_counts_strict_pairs(self):
        for length in range(7):
            for values in itertools.product((1, 2, inputs.COUNT), repeat=length):
                self.assertEqual(inputs.oracle(values), quadratic(values))
        self.assertEqual(inputs.encode([]), b"0\n\n")
        self.assertEqual(inputs.encode([4, 1, 1]), b"3\n4 1 1\n")

    def test_orders_preserve_each_windows_ranks(self):
        shuffled = inputs.workload("shuffled")
        runs = inputs.workload("runs")
        self.assertEqual(len(shuffled), inputs.COUNT)
        self.assertEqual(len(runs), inputs.COUNT)
        self.assertTrue(all(1 <= rank <= inputs.COUNT for rank in shuffled))
        self.assertLess(len(set(shuffled)), len(shuffled))
        self.assertNotEqual(shuffled, runs)
        for start in range(0, inputs.COUNT, inputs.WINDOW):
            end = start + inputs.WINDOW
            expected = sorted(shuffled[start:end], reverse=(start // inputs.WINDOW) % 2 == 1)
            self.assertEqual(runs[start:end], expected)

    def test_cpp_solvers(self):
        cases = [[], [inputs.COUNT], [1, 1, 1], [inputs.COUNT, 1, inputs.COUNT, 1],
                 [5, 3, 1, 4, 2, 4], list(range(17, 0, -1))]
        samples = [(inputs.encode(values), quadratic(values)) for values in cases]
        descending = list(range(inputs.COUNT, 0, -1))
        expected = f"{inputs.COUNT * (inputs.COUNT - 1) // 2}\n".encode("ascii")
        self.assertEqual(inputs.oracle(descending), expected)
        samples.append((inputs.encode(descending), expected))
        with tempfile.TemporaryDirectory() as temp:
            for solver in ("MERGE", "FENWICK", "SEGTREE"):
                binary = Path(temp) / solver
                subprocess.run([os.environ.get("CXX", "c++"), "-std=c++20", "-O2", "-DSOLVER_" + solver,
                                HERE / "inversions.cpp", "-o", binary], check=True, timeout=60)
                for data, answer in samples:
                    with self.subTest(solver=solver, size=len(data)):
                        self.assertEqual(subprocess.check_output([binary], input=data, timeout=15), answer)


if __name__ == "__main__":
    unittest.main()

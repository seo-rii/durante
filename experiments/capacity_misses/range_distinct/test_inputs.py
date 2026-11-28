import itertools
from pathlib import Path
import subprocess
import tempfile
import unittest

import inputs


class RangeDistinctInputs(unittest.TestCase):
    def test_oracle(self):
        for n in range(1, 7):
            queries = [(l, r) for l in range(n) for r in range(l, n)]
            for a in itertools.product((0, 255), repeat=n):
                expected = "".join(f"{len(set(a[l:r+1]))}\n" for l, r in queries).encode()
                self.assertEqual(inputs.oracle(a, queries), expected)
        self.assertEqual(inputs.oracle([4], []), b"")

    def test_order(self):
        data, original = inputs.workload("shuffled")
        same_data, ordered = inputs.workload("runs")
        self.assertEqual(data, same_data)
        self.assertEqual(len(original), inputs.SIZE)
        self.assertNotEqual(original, ordered)
        for start in range(0, len(original), inputs.WINDOW):
            block = slice(start, start + inputs.WINDOW)
            self.assertEqual(ordered[block], sorted(original[block], reverse=(start // inputs.WINDOW) % 2 == 1))

    def test_compiled_solvers_preserve_query_order(self):
        data = [((i * 73) ^ (i // 5)) % 256 for i in range(403)]
        qs = [(400, 401), (0, 255), (127, 256), (128, 255), (0, 402), (0, 0), (0, 255), (83, 311)]
        expected = "".join(f"{len(set(data[l:r+1]))}\n" for l, r in qs).encode()
        with tempfile.TemporaryDirectory() as tmp:
            for mode in ("FENWICK", "MO", "BITSET"):
                exe = Path(tmp) / mode
                subprocess.run(["c++", "-std=c++20", "-O2", "-DSOLVER_" + mode, Path(__file__).with_name("queries.cpp"),
                                "-o", exe], check=True, timeout=45)
                self.assertEqual(subprocess.check_output([exe], input=inputs.encode(data, qs), timeout=10), expected)

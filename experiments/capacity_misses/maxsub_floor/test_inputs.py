import itertools
from pathlib import Path
import runpy
import unittest

inputs = runpy.run_path(str(Path(__file__).with_name("inputs.py")))


class InputTests(unittest.TestCase):
    def test_composition_including_negative_sums(self):
        for n in range(1, 7):
            for values in itertools.product((-5, 0, 3), repeat=n):
                expected = max(sum(values[i:j]) for i in range(n) for j in range(i + 1, n + 1))
                self.assertEqual(inputs["best"](values), expected)

    def test_registered_prefix_and_encoding(self):
        short, seed = inputs["workload"]("short")
        long, same_seed = inputs["workload"]("long")
        self.assertEqual((short, long, seed), (32768, 2097152, same_seed))
        self.assertEqual(list(inputs["numbers"](short, seed)), list(itertools.islice(inputs["numbers"](long, same_seed), short)))
        self.assertEqual(inputs["encode"](5, 7), b'5 7\n')

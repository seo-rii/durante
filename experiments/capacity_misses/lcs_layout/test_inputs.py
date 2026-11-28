import itertools
import unittest
import runpy
from pathlib import Path

inputs = runpy.run_path(str(Path(__file__).with_name("inputs.py")))


class InputTests(unittest.TestCase):
    def test_bit_oracle_with_exhaustive_subsequences(self):
        words = [bytes(bits) for n in range(5) for bits in itertools.product(b'ab', repeat=n)]
        def subsequences(s):
            return {bytes(s[i] for i in range(len(s)) if mask >> i & 1) for mask in range(1 << len(s))}
        sets = {w: subsequences(w) for w in words}
        for a, b in itertools.product(words, repeat=2):
            self.assertEqual(int(inputs["answer"](a, b)), max(map(len, sets[a] & sets[b])))

    def test_transpose(self):
        a, b = inputs["workload"]("wide")
        self.assertEqual((len(a), len(b)), (1152, 2304))
        self.assertEqual((b, a), inputs["workload"]("tall"))
        self.assertEqual(inputs["answer"](a, b), inputs["answer"](b, a))

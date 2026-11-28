from pathlib import Path
import runpy
import unittest

HERE = Path(__file__).resolve().parent
data = runpy.run_path(str(HERE / "inputs.py"))


class InputTest(unittest.TestCase):
    def test_oracle_includes_overlaps(self):
        cases = [(b'aa', b'aaaa', b'3\n'), (b'ab', b'abccab', b'2\n'),
                 (b'aa', b'a', b'0\n'), (b'x', b'', b'0\n')]
        for pattern, text, expected in cases:
            self.assertEqual(data["answer"](pattern, text), expected)

    def test_registered_shape(self):
        for name in ("random", "periodic"):
            pattern, text = data["workload"](name)
            self.assertEqual((len(pattern), len(text)), (48, 1 << 20))
            self.assertEqual(data["answer"](pattern, text), b'128\n')

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


class DeadlineTest(unittest.TestCase):
    def test_bidirectional_criterion(self):
        from fractions import Fraction
        import replay
        I = replay.Interval
        unit = I(Fraction(1), Fraction(1))
        half = I(Fraction(1, 2), Fraction(1, 2))
        two = I(Fraction(2), Fraction(2))
        mixed = I(half.low, two.high)
        Observation = replay.intervals["Observation"]
        def row(name, native, model):
            return Observation("string_matching", "test", name, native, model)
        def passes(rows):
            return replay.evaluate(rows, unit, [Fraction(1)])["passes_bidirectional_criterion"]
        accepted = row("equal", unit, unit)
        timeout = row("above", two, two)
        self.assertTrue(passes([accepted, timeout]))
        self.assertFalse(passes([accepted]))
        self.assertFalse(passes([timeout]))
        self.assertFalse(passes([accepted, timeout, row("wrong", unit, two)]))
        self.assertFalse(passes([accepted, timeout, row("mixed", mixed, half)]))

    def test_published_results(self):
        import replay
        self.assertEqual(replay.replay(), replay.read(HERE / "results.json"))

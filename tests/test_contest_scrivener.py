import random
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from contest_warm import scrivener


class Scrivener(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.binary = Path(temp.name) / "scrivener"
        subprocess.run(["g++", "-std=c++20", "-O2", ROOT / "experiments/contest_warm/scrivener.cpp",
                        "-o", cls.binary], check=True)

    def check(self, data, expected):
        self.assertEqual(subprocess.check_output([self.binary], input=data, text=True), expected)

    def test_undo_an_undo_and_ignore_reads(self):
        self.check("8\nT x\nT y\nP 0\nU 1\nT z\nU 2\nP 1\nP 0\n", "x\ny\nx\n")

    def test_matches_copied_strings(self):
        rng = random.Random(3092)
        history, ops, expected = [""], [], []
        for _ in range(2000):
            kind = rng.choice("TTUP")
            if kind == "U" and len(history) > 1:
                undo = rng.randrange(1, len(history))
                ops.append(("U", undo))
                history.append(history[-1-undo])
            elif kind == "P" and history[-1]:
                pos = rng.randrange(len(history[-1]))
                expected.append(history[-1][pos] + "\n")
                ops.append(("P", pos))
            else:
                letter = rng.choice("abcd")
                ops.append(("T", letter))
                history.append(history[-1] + letter)
        data = f"{len(ops)}\n" + "".join(f"{op} {value}\n" for op, value in ops)
        self.assertEqual(scrivener.answers(ops), "".join(expected))
        self.check(data, "".join(expected))

    def test_registered_text_streams(self):
        for name in scrivener.CASES:
            with self.subTest(name=name):
                self.check(*scrivener.make(name))

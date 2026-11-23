from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from contest_warm.scrivener import answers


class ScrivenerLifting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.binary = Path(temp.name) / "scrivener_lifting"
        source = ROOT / "experiments/contest_submissions/scrivener_lifting.cpp"
        subprocess.run(["g++", "-std=c++20", "-O2", source, "-o", cls.binary], check=True)

    def check(self, operations, expected):
        text = f"{len(operations)}\n" + "".join(f"{op} {value}\n" for op, value in operations)
        self.assertEqual(subprocess.check_output([self.binary], input=text, text=True), expected)

    def test_power_of_two_ancestors(self):
        text = "".join(chr(97 + (i * 17 + i // 29) % 26) for i in range(16385))
        operations = [("T", letter) for letter in text]
        positions = [len(text) - 1 - distance for distance in range(len(text))]
        operations.extend(("P", position) for position in positions)
        self.check(operations, "".join(text[position] + "\n" for position in positions))

    def test_branching_histories_match_offline_tree_oracle(self):
        rng = random.Random(201206)
        operations = []
        depths = [0]
        for _ in range(5000):
            command = rng.choice("TTTUPP")
            if command == "U" and len(depths) > 1:
                undo = rng.randrange(1, len(depths))
                operations.append(("U", undo))
                depths.append(depths[-1 - undo])
            elif command == "P" and depths[-1]:
                operations.append(("P", rng.randrange(depths[-1])))
            else:
                operations.append(("T", rng.choice("abcdefghijklmnopqrstuvwxyz")))
                depths.append(depths[-1] + 1)
        self.check(operations, answers(operations))

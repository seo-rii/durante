"""Check the Scrivener order experiments and their standalone input scanner."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXPERIMENTS))
from scrivener_order.corpus import count_queries
from scrivener_pages import corpus


class ScrivenerPagesCorpus(unittest.TestCase):
    def test_retains_hashes_of_both_calibration_inputs(self):
        archived = json.loads((EXPERIMENTS / "scrivener_cache/plan.json").read_text())["cases"]
        for row in archived[:2]:
            text, answer = corpus.make(row["name"])
            self.assertEqual(sha256(text.encode()).hexdigest(), row["stdin_sha256"])
            self.assertEqual(sha256(answer.encode()).hexdigest(), row["stdout_sha256"])

    def test_permutations_oracle_and_lookup_work(self):
        for size in (1, 19, 4099):
            orders = {}
            for name in corpus.NAMES:
                text, answer = corpus.make(name, size)
                header, *commands = text.splitlines()
                self.assertEqual(int(header), 2 * size)
                letters = "".join(cmd.split()[1] for cmd in commands[:size])
                queries = [int(cmd.split()[1]) for cmd in commands[size:]]
                self.assertEqual(answer.splitlines(), [letters[i] for i in queries])
                if name == "ascending":
                    prefix = commands[:size]
                    multiset = Counter(queries)
                    steps = count_queries(text)
                self.assertEqual(commands[:size], prefix)
                self.assertEqual(Counter(queries), multiset)
                self.assertEqual(count_queries(text), steps)
                orders[name] = queries
            asc = orders["ascending"]
            self.assertEqual(orders["alternate_extremes"][::2], asc[:(size + 1) // 2])
            self.assertEqual(orders["alternate_extremes"][1::2], asc[::-1][:size // 2])
            if size == 4099:
                self.assertEqual(len({tuple(v) for v in orders.values()}), len(corpus.NAMES))
                blocks = [asc[i:i + 512] for i in range(0, size, 512)]
                remaining = orders["shuffled_blocks"]
                while blocks:
                    block = next(b for b in blocks if remaining[:len(b)] == b)
                    remaining = remaining[len(block):]
                    blocks.remove(block)
                self.assertFalse(remaining)

    def test_bit_reversal_and_lane_orders_on_ranks(self):
        self.assertEqual(list(corpus._ranks("bit_reverse", 7)), [0, 4, 2, 6, 1, 5, 3])
        for lanes in (16, 32):
            size = 2 * lanes + 1
            starts = [i * size // lanes for i in range(lanes)]
            self.assertEqual(list(corpus._ranks(f"lanes{lanes}", size)),
                             starts + [start + 1 for start in starts] + [size - 1])
        with self.assertRaises(ValueError):
            corpus.make("unknown", 8)
        with self.assertRaises(ValueError):
            corpus.make("bit_reverse", 0)


class ScrivenerPageScanner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.binary = Path(directory.name) / "scan"
        subprocess.run(["g++", "-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror",
                        str(EXPERIMENTS / "scrivener_pages/scan.cpp"), "-o", cls.binary], check=True)

    def check(self, text, expected):
        self.assertEqual(corpus.features(text), dict(zip(("accesses", "misses", "position_bits"),
                                                         expected, strict=True)))
        self.assertEqual(subprocess.check_output([self.binary], text=True, input=text),
                         "{} {} {}\n".format(*expected))

    def test_page_edges_refresh_and_capacity(self):
        positions = [i * 512 for i in range(16)] + [0, 511, 16 * 512, 0, 512]
        length = max(positions) + 1
        text = (f"{length + len(positions)}\n" + "T a\n" * length +
                "".join(f"P {p}\n" for p in positions))
        bits = sum(abs(a - b).bit_length() for a, b in zip(positions, positions[1:]))
        self.check(text, (len(positions), 18, bits))
        self.check("6\nT x\nT y\nP 1\nT z\nP 1\nP 0\n", (3, 1, 1))
        self.check("0\n", (0, 0, 0))

    def test_production_orders_match_the_reference(self):
        for name in corpus.NAMES:
            text, _ = corpus.make(name, 13_123)
            features = corpus.features(text)
            with self.subTest(name=name):
                self.check(text, (features["accesses"], features["misses"], features["position_bits"]))

    def test_rejects_malformed_inputs_and_arguments(self):
        for text in ("", "-1", "2147483648", "1 T", "1 T xy", "1 T c junk", "2 T p P 1",
                     "1 P 0", "1 U 0", "2 T x P -1", "1Tx", "0x", "1 T Q", "1 T é"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                corpus.features(text)
            result = subprocess.run([self.binary], text=True, input=text, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
        result = subprocess.run([self.binary, "--unexpected"], text=True, input="0", capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()

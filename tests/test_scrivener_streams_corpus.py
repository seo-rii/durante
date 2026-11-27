"""Exercise the stream permutations, character oracle and per-page return distance."""
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
from scrivener_streams import corpus


def input_for(positions):
    length = max(positions) + 1
    return f"{length + len(positions)}\n" + "T a\n" * length + "".join(f"P {p}\n" for p in positions)


def expected_counts(positions):
    # Reuse distance counts distinct intervening pages since the last visit.
    # This detects eviction without using the scanner's 16-slot LRU algorithm.
    last_index = {}
    page_sequence = [p // 512 for p in positions]
    misses = disruptive = 0
    for index, page in enumerate(page_sequence):
        if page not in last_index:
            misses += 1
            disruptive += 1
        else:
            last = last_index[page]
            if len(set(page_sequence[last + 1:index])) >= 16:
                misses += 1
                disruptive += abs(positions[index] - positions[last]) > 8
        last_index[page] = index
    bits = sum(abs(a - b).bit_length() for a, b in zip(positions, positions[1:]))
    return (len(positions), misses, disruptive, bits)


class StreamCorpus(unittest.TestCase):
    def test_registered_anchors_and_repeat_input(self):
        old_cases = json.loads((EXPERIMENTS / "scrivener_pages/plan.json").read_bytes())["cases"]
        for case in (c for c in old_cases if c["name"] in ("ascending", "shuffled", "lanes32")):
            text, answers = corpus.make(case["name"])
            self.assertEqual(sha256(text.encode()).hexdigest(), case["stdin_sha256"])
            self.assertEqual(sha256(answers.encode()).hexdigest(), case["stdout_sha256"])

    def test_all_orders_preserve_queries_work_and_character_answers(self):
        for size in (1, 29, 519, 6151):
            original, _ = corpus.make("ascending", size)
            base = original.splitlines()
            variants = set()
            for name in corpus.NAMES:
                text, answers = corpus.make(name, size)
                commands = text.splitlines()
                self.assertEqual(commands[:size + 1], base[:size + 1])
                self.assertEqual(Counter(commands[size + 1:]), Counter(base[size + 1:]))
                typed = "".join(c.split()[1] for c in commands[1:size + 1])
                oracle = [typed[int(c.split()[1])] for c in commands[size + 1:]]
                self.assertEqual(answers.splitlines(), oracle)
                self.assertEqual(count_queries(text), count_queries(original))
                variants.add(text)
            if size == 6151:
                self.assertEqual(len(variants), len(corpus.NAMES))

    def test_lane_orientation_tiles_and_bit_reversal(self):
        # A multiple of 48 gives equal length lanes for slicing the round-robin output.
        size = 48 * 513
        for name in ("descending48", "tiles64_lanes48", "tiles512_lanes48"):
            ranks = list(corpus._ranks(name, size))
            for lane in range(48):
                segment = ranks[lane::48]
                first = 513 * lane
                if name == "descending48":
                    self.assertEqual(segment, list(range(first + 512, first - 1, -1)))
                else:
                    width = 64 if name == "tiles64_lanes48" else 512
                    for offset in range(0, 513, width):
                        self.assertEqual(sorted(segment[offset:offset + width]),
                                         list(range(first + offset, first + min(513, offset + width))))
        ranks = list(corpus._ranks("bitrev_tiles512", 519))
        self.assertEqual(ranks[:8], [0, 256, 128, 384, 64, 320, 192, 448])
        self.assertEqual(ranks[512:], [512, 516, 514, 518, 513, 517, 515])
        with self.assertRaises(ValueError):
            corpus.make("unknown", 123)


class StreamScanner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.binary = Path(tmp.name) / "scan"
        subprocess.run(["g++", "-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror",
                        EXPERIMENTS / "scrivener_streams/scan.cpp", "-o", cls.binary], check=True)

    def check(self, text, expected):
        self.assertEqual(corpus.features(text), dict(zip(("accesses", "misses", "disruptive", "position_bits"),
                                                         expected, strict=True)))
        output = subprocess.check_output([self.binary], input=text, text=True)
        self.assertEqual(output, "{} {} {} {}\n".format(*expected))

    def test_thresholds_evictions_and_in_page_hits(self):
        others = [512 * p for p in range(1, 17)]
        for positions in ([17], [511, 512, 513, 511],
                          [200, 100, *others, 108, *others, 117],
                          [90, 102, *others[:15], 2, *others, 0],
                          [25, 17, *others, 9, *others, 0]):
            expected = expected_counts(positions)
            if len(positions) == 36:
                self.assertEqual(expected[:3], (36, 35, 18))
            self.check(input_for(positions), expected)
        self.check("0\n", (0, 0, 0, 0))

    def test_every_order_matches_the_independent_reuse_count(self):
        for name in corpus.NAMES:
            text, _ = corpus.make(name, 10_151)
            positions = [int(line[2:]) for line in text.splitlines() if line.startswith("P ")]
            with self.subTest(name=name):
                self.check(text, expected_counts(positions))

    def test_rejects_invalid_inputs(self):
        for text in ("", "-1", "2147483648", "1 T", "1 T ab", "1 U 0", "2 T x P 1",
                     "0 unexpected", "1Tx", "2 T a P -1", "1 T 3", "1 T é"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    corpus.features(text)
                proc = subprocess.run([self.binary], input=text, text=True, capture_output=True)
                self.assertNotEqual(proc.returncode, 0)
                self.assertFalse(proc.stdout)
        self.assertNotEqual(subprocess.run([self.binary, "extra"], input=b"0").returncode, 0)


if __name__ == "__main__":
    unittest.main()

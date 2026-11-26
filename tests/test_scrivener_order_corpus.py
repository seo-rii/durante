"""Check the Scrivener permutations, their answers and both structural query counts."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sys
import unittest

EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXPERIMENTS))
from scrivener_order import corpus


class ScrivenerOrderCorpus(unittest.TestCase):
    def test_only_query_order_changes(self):
        size = 2 * corpus.TILE_SIZE + 31
        samples = {}
        compacts = set()
        for name in corpus.NAMES[:3]:
            text, answer = corpus.matched_case(name, size)
            header, *commands = text.splitlines()
            self.assertEqual(int(header), len(commands))
            typed = commands[:size]
            self.assertTrue(all(cmd.startswith("T ") for cmd in typed))
            self.assertTrue(all(cmd.startswith("P ") for cmd in commands[size:]))
            prefix = "".join(cmd.split()[1] for cmd in typed)
            positions = [int(cmd.split()[1]) for cmd in commands[size:]]
            self.assertEqual(len(positions), size)
            self.assertEqual(answer.splitlines(), [prefix[p] for p in positions])
            samples[name] = (prefix, positions)
            compacts.add(tuple(corpus.count_queries(text).items()))

        prefix, positions = samples["ascending"]
        self.assertEqual(sorted(positions), positions)
        self.assertEqual((positions[0], positions[-1]), (0, size - 1))
        self.assertLess(len(set(positions)), size)
        for other_prefix, other_positions in samples.values():
            self.assertEqual(prefix, other_prefix)
            self.assertEqual(Counter(positions), Counter(other_positions))
        tiled = samples["tiled"][1]
        for i in range(0, size, corpus.TILE_SIZE):
            self.assertEqual(sorted(tiled[i:i + corpus.TILE_SIZE]), positions[i:i + corpus.TILE_SIZE])
        self.assertNotEqual(tiled, positions)
        shuffled = samples["shuffled"][1]
        self.assertNotEqual(sorted(shuffled[:corpus.TILE_SIZE]), positions[:corpus.TILE_SIZE])
        self.assertEqual(len(compacts), 1)

    def test_jump_counts(self):
        # At depth eight, the compact jump lengths from depths 8,7,6,5,4 are 1,7,3,1,1.
        # Queries for positions 0, 5, 7 finish in 5, 2, 0 compact steps and 3, 1, 0 lifting steps.
        text = "11\n" + "T a\n" * 8 + "P 0\nP 5\nP 7\n"
        self.assertEqual(corpus.count_queries(text), dict(type_commands=8, print_commands=3,
                         unique_positions=3, compact_query_steps=7, lifting_query_steps=4))
        with self.assertRaisesRegex(ValueError, "outside"):
            corpus.count_queries("1\nP 0\n")

    def test_historical_control_uses_the_published_bytes(self):
        cases = json.loads((EXPERIMENTS / "contest_submissions/plan.json").read_bytes())["cases"]
        case = next(c for c in cases if c["case_id"] == "scrivener/interwoven_pages")
        text, answer = corpus.make("interwoven_pages")
        self.assertEqual(sha256(text.encode()).hexdigest(), case["stdin_sha256"])
        self.assertEqual(sha256(answer.encode()).hexdigest(), case["stdout_sha256"])

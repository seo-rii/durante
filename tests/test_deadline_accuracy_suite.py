"""Small independent output oracles for the executable workloads in the accuracy corpus."""
from collections import deque
import math
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1] / "experiments/deadline_accuracy"


class WorkloadOutputs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.bin = Path(cls.temp.name)
        for name in ("threshold_runs", "successor_walk", "rational_gcd", "csr_frontier"):
            subprocess.run(["g++", "-std=c++20", "-O2", ROOT / (name + ".cpp"), "-o", cls.bin / name], check=True)

    def output(self, name, *fields):
        result = subprocess.run([self.bin / name], input=" ".join(map(str, fields)), text=True,
                                capture_output=True, check=True)
        self.assertEqual(result.stderr, "")
        return [int(word) for word in result.stdout.split()]

    def test_threshold_runs_match_exhaustive_subarray_count(self):
        events = [i // 5 % 2 for i in range(18)] * 3
        ranges = [end-start for start in range(len(events)) for end in range(start+1, len(events)+1)
                  if all(events[start:end])]
        self.assertEqual(self.output("threshold_runs", 18, 3, 2), [max(ranges), len(ranges)])

    def test_successor_walk_visits_each_vertex_once(self):
        for block_bits in (0, 3, 8):
            with self.subTest(block_bits=block_bits):
                self.assertEqual(self.output("successor_walk", 8, block_bits, 512), [0, 2 * sum(range(256))])

    def test_rational_gcd_matches_exact_arithmetic(self):
        mask = 2**64 - 1
        for distribution in range(3):
            state = 0x589146c7ab810e69
            values = []
            for _ in range(50):
                pair = []
                for _ in range(2):
                    state ^= (state << 13) & mask
                    state ^= state >> 7
                    state ^= (state << 17) & mask
                    pair.append(state)
                a, b = pair[0], pair[1] | 1
                if distribution == 0:
                    b &= 255
                elif distribution == 2:
                    scale = (a % 2**20) + 1
                    a, b = 267914296 * scale, 165580141 * scale
                values.append(math.gcd(a, b))
            with self.subTest(distribution=distribution):
                self.assertEqual(self.output("rational_gcd", 50, distribution), [sum(values)])

    def test_csr_bfs_matches_distances_after_relabeling(self):
        n = 64
        total = 0
        for t in range(4):
            start = (t * 104729) % n
            dist = {start: 0}
            pending = deque([start])
            while pending:
                u = pending.popleft()
                hops = [1, 17 + u % 64] + ([251] if u % 2 else [])
                for hop in hops:
                    v = (u + hop) % n
                    if v not in dist:
                        dist[v] = dist[u] + 1
                        pending.append(v)
            self.assertEqual(len(dist), n)
            total += sum(dist.values())
        for permutation in (0, 1):
            self.assertEqual(self.output("csr_frontier", 6, 4, permutation), [total])

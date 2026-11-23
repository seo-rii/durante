"""Check weighted forest completion on tiny graphs against all possible new trails."""
from itertools import combinations
from pathlib import Path
from random import Random
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from contest_warm import dreaming


def exhaustive(n, edges, length):
    existing = {tuple(sorted((a, b))) for a, b, _ in edges}
    candidates = [pair for pair in combinations(range(n), 2) if pair not in existing]
    best = None
    for added in combinations(candidates, n - 1 - len(edges)):
        graph = [[] for _ in range(n)]
        for a, b, weight in [*edges, *((a, b, length) for a, b in added)]:
            graph[a].append((b, weight))
            graph[b].append((a, weight))
        diameter = 0
        for root in range(n):
            distance = {root: 0}
            todo = [root]
            while todo:
                vertex = todo.pop()
                for other, weight in graph[vertex]:
                    if other not in distance:
                        distance[other] = distance[vertex] + weight
                        todo.append(other)
            if len(distance) != n:
                break
            diameter = max(diameter, max(distance.values()))
        else:
            if best is None or diameter < best:
                best = diameter
    return best


class DreamingCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.binary = Path(tmp.name) / "dreaming"
        subprocess.run(["g++", "-std=c++20", "-O2", ROOT / "experiments/contest_warm/dreaming.cpp",
                        "-o", cls.binary], check=True)

    def test_all_small_trail_completions(self):
        rng = Random(4597)
        forests = [(1, [], 4), (5, [], 10_000), (5, [(1, 2, 3), (0, 3, 5000)], 1)]
        for _ in range(25):
            n = rng.randrange(2, 7)
            edges = [(v, rng.randrange(v), rng.randrange(1, 11)) for v in range(1, n) if rng.random() < 0.6]
            forests.append((n, edges, rng.randrange(1, 8)))
        for n, edges, length in forests:
            with self.subTest(n=n, edges=edges, length=length):
                expected = exhaustive(n, edges, length)
                self.assertEqual(dreaming.connected_diameter(n, edges, length), expected)
                data = f"{n} {len(edges)} {length}\n" + "".join(f"{a} {b} {w}\n" for a,b,w in edges)
                self.assertEqual(subprocess.check_output([self.binary], input=data, text=True), f"{expected}\n")

    def test_full_forests_and_bounds(self):
        for name in dreaming.CASES:
            with self.subTest(name=name):
                data, answer = dreaming.make(name)
                header, *trails = data.splitlines()
                n, m, length = map(int, header.split())
                self.assertEqual(len(trails), m)
                self.assertTrue(0 <= m < n <= 100_000 and 1 <= length <= 10_000)
                sets = list(range(n))
                def root(vertex):
                    while sets[vertex] != vertex:
                        sets[vertex] = sets[sets[vertex]]
                        vertex = sets[vertex]
                    return vertex
                for trail in trails:
                    a, b, w = map(int, trail.split())
                    self.assertTrue(0 <= a < n and 0 <= b < n and 1 <= w <= 10_000)
                    x, y = root(a), root(b)
                    self.assertNotEqual(x, y)
                    sets[x] = y
                self.assertEqual(subprocess.check_output([self.binary], input=data, text=True), answer)

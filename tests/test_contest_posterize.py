"""Enumerate palettes on small histograms and check the independent integer palette oracle."""
from itertools import combinations
from pathlib import Path
from random import Random
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from contest_warm import posterize


class PosterizeCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.binary = Path(tmp.name) / "posterize"
        subprocess.run(["g++", "-std=c++20", "-O2", ROOT / "experiments/contest_warm/posterize.cpp",
                        "-o", cls.binary], check=True)

    def test_exhaustive_palettes_on_small_histograms(self):
        rng = Random(6317)
        cases = [([(0, 2**26), (255, 2**26)], 1), ([(3, 5), (6, 5)], 1)]
        for _ in range(18):
            levels = sorted(rng.sample(range(12), rng.randrange(3, 8)))
            cases.append(([(r, rng.randint(1, 100)) for r in levels], rng.randrange(1, len(levels))))
        for histogram, k in cases:
            with self.subTest(histogram=histogram, k=k):
                span = range(histogram[0][0], histogram[-1][0]+1)
                expected = min(sum(count * min((red - v)**2 for v in palette) for red, count in histogram)
                               for palette in combinations(span, k))
                self.assertEqual(posterize.palette_error(histogram, k), expected)
                text = f"{len(histogram)} {k}\n" + "".join(f"{r} {p}\n" for r, p in histogram)
                self.assertEqual(subprocess.check_output([self.binary], input=text, text=True), f"{expected}\n")

    def test_full_histograms_and_bounds(self):
        for name in posterize.CASES:
            with self.subTest(name=name):
                data, expected = posterize.make(name)
                header, *rows = data.splitlines()
                d, k = map(int, header.split())
                self.assertTrue(1 <= k < d <= 256)
                self.assertEqual(len(rows), d)
                prev = -1
                for row in rows:
                    red, pixels = map(int, row.split())
                    self.assertTrue(prev < red <= 255 and 1 <= pixels <= 2**26)
                    prev = red
                self.assertEqual(subprocess.check_output([self.binary], input=data, text=True), expected)

"""Check the 16-page Wall feature independently of the timing experiments."""
from collections import OrderedDict
from pathlib import Path
from random import Random
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WallLocalityProbe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.binary = Path(directory.name) / "leaf_pages"
        subprocess.run(["g++", "-std=c++20", "-O2", ROOT / "experiments/wall_locality/leaf_pages.cpp",
                        "-o", cls.binary], check=True)

    def check_ranges(self, n, ranges):
        recent = OrderedDict()
        misses = 0
        lines = [f"{n} {len(ranges)}"]
        for i, (left, right) in enumerate(ranges):
            lines.append(f"{1 + i % 2} {left} {right} {100000 if i % 2 else 0}")
            for position in (left, right):
                page = position // 512
                if page not in recent:
                    misses += 1
                    if len(recent) == 16:
                        recent.popitem(last=False)
                recent[page] = None
                recent.move_to_end(page)
        output = subprocess.check_output([self.binary], input="\n".join(lines), text=True)
        self.assertEqual(output, f"{2 * len(ranges)} {misses}\n")
        return misses

    def test_lru_refresh_eviction_page_boundary_and_long_range(self):
        # Rereading page zero makes page one the oldest of the first sixteen.
        pages = [*range(16), 0, 16, 0, 1]
        self.assertEqual(self.check_ranges(17 * 512, [(p * 512, p * 512 + 511) for p in pages]), 18)
        self.assertEqual(self.check_ranges(2_000_000, [(511, 512), (0, 1_999_999)]), 3)

    def test_random_ranges_match_ordered_map(self):
        rng = Random(100)
        n = 25000
        ranges = [sorted((rng.randrange(n), rng.randrange(n))) for _ in range(300)]
        self.check_ranges(n, ranges)

    def test_rejects_invalid_headers_records_and_trailing_data(self):
        samples = ["0 1", "2000001 1", "8 0", "8 500001", "4 1\n1 0 0",
                   "2 1\n3 0 1 5", "2 1\n2 1 0 5", "2 1\n1 0 2 0", "2 1\n1 -1 0 0",
                   "2 1\n1 0 1 100001", "2 1\n1 0 1 4junk", "2 1\n1 0 1 3 0",
                   "9999999999999999999999 1", "2 1\n1 0+1 4"]
        for sample in samples:
            with self.subTest(sample=sample):
                result = subprocess.run([self.binary], input=sample, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")

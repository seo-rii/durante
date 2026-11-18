from itertools import accumulate
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


FIXTURES = Path(__file__).resolve().parents[1] / "experiments" / "time_features" / "heldout"
# Frozen before collecting native times or counter measurements.
CASES = {
    "lower_bound": ((196608, 2533137832223807), (393216, 10133175259229984),
                    (786432, 40532096028917760)),
    "window_sum": ((16777216, 13817628634304741376), (33554432, 18376560122177519616),
                   (67108864, 18165075732336738304)),
}


def lower_bound_checksum(queries):
    # Only the entry in the query's own four-number interval can decide
    # between the two possible answers. No search or sorted table is needed.
    checksum = 0
    for i in range(queries):
        key = 4 * ((40503 * i + 44257) % (1 << 18)) + (i // 32) % 4
        bucket, offset = divmod(key, 4)
        entry_offset = (((bucket * 2654435761) // 65536) ^ bucket) % 4
        checksum += (i + 1) * (bucket + (entry_offset < offset))
    return checksum


def window_sum_checksum(steps):
    # Determine each window directly by prefix differences. Across successive
    # periods only the weight of a given position changes.
    size = 65536
    width = 251
    values = [(i * 25173 + 13849) % size for i in range(size)]
    prefix = list(accumulate(values[-(width - 1):] + values, initial=0))
    windows = [prefix[i + width] - prefix[i] for i in range(size)]
    periods, tail = divmod(steps, size)
    first = sum(i * window for i, window in enumerate(windows, start=1))
    checksum = periods * first + size * sum(windows) * periods * (periods - 1) // 2
    checksum += sum((periods * size + i) * w for i, w in enumerate(windows[:tail], start=1))
    return checksum % (1 << 64)


class HeldoutFixtureTests(unittest.TestCase):
    def test_independent_oracles_match_frozen_checksums(self):
        for name, oracle in (("lower_bound", lower_bound_checksum), ("window_sum", window_sum_checksum)):
            for steps, expected in CASES[name]:
                with self.subTest(name=name, steps=steps):
                    self.assertEqual(oracle(steps), expected)

    @unittest.skipUnless(shutil.which("g++"), "requires a C++20 compiler")
    def test_program_stdout_matches_each_frozen_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, cases in CASES.items():
                binary = Path(tmp) / name
                subprocess.run(["g++", "-O2", "-Wall", "-std=c++20", FIXTURES / f"{name}.cpp",
                                "-o", binary], check=True, timeout=30)
                for steps, expected in cases:
                    with self.subTest(name=name, steps=steps):
                        run = subprocess.run([binary], input=f"{steps}\n".encode("ascii"),
                                             capture_output=True, check=True, timeout=10)
                        self.assertEqual(run.stdout, f"{expected}\n".encode("ascii"))
                        self.assertEqual(run.stderr, b"")


if __name__ == "__main__":
    unittest.main()

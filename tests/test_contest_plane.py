"""Check the live timetable solver against all next-bus choices on small schedules."""
from functools import lru_cache
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from contest_accuracy import plane


class CatchThePlane(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.binary = Path(temporary.name) / "plane"
        subprocess.run(["g++", "-std=c++20", "-O2", ROOT / "experiments/contest_accuracy/plane.cpp",
                        "-o", cls.binary], check=True)

    def solve(self, data):
        return subprocess.run([self.binary], capture_output=True, input=data, text=True, check=True).stdout

    def test_cannot_transfer_or_retry_at_equal_time(self):
        data = "5 3\n1000000000000000000\n0 2 0 4 1\n0 1 0 5 0.25\n0 1 0 1 0.25\n2 1 4 5 1\n2 1 5 1000000000000000000 0.20\n"
        self.assertEqual(self.solve(data), "0.250000000\n")

    def test_all_next_bus_choices_on_small_schedules(self):
        rng = random.Random(86467)
        for _ in range(32):
            stations = rng.randrange(2, 7)
            buses = []
            for _ in range(rng.randrange(1, 32)):
                origin, destination = rng.sample(range(stations), 2)
                depart = rng.randrange(8)
                arrive = rng.randrange(depart + 1, 12)
                buses.append((origin, destination, depart, arrive, rng.randrange(9)))

            @lru_cache(None)
            def enumerate_actions(station, after):
                if station == 1:
                    return 1.0
                best = 0.0
                for origin, dest, dep, arr, eighths in buses:
                    if station == origin and dep > after:
                        chance = eighths / 8
                        best = max(best, chance * enumerate_actions(dest, arr)
                                   + (1 - chance) * enumerate_actions(station, dep))
                return best

            expected = enumerate_actions(0, -1)
            with self.subTest(stations=stations, buses=buses):
                data = f"{len(buses)} {stations}\n12\n" + "".join(
                    f"{a} {b} {s} {t} {p / 8}\n" for a, b, s, t, p in buses)
                self.assertAlmostEqual(float(self.solve(data)), expected, delta=10**-7)
                self.assertEqual(float(plane.probability(stations, buses)), expected)

    def test_registered_exact_answers_and_schedule_bounds(self):
        for name in plane.CASES:
            with self.subTest(name=name):
                text, expected = plane.make(name)
                first, second, *routes = text.splitlines()
                count, stations = map(int, first.split())
                deadline = int(second)
                self.assertEqual(count, len(routes))
                self.assertTrue(1 <= count <= 10**6 and 2 <= stations <= 10**6 and 1 <= deadline <= 10**18)
                for route in routes:
                    a, b, s, t, p = route.split()
                    self.assertTrue(0 <= int(a) < stations and 0 <= int(b) < stations and a != b)
                    self.assertTrue(0 <= int(s) < int(t) <= deadline and 0 <= float(p) <= 1)
                self.assertEqual(self.solve(text), expected)


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SOURCES = Path(__file__).resolve().parents[1] / "experiments/time_features/heldout"
# These inputs and stdout values were fixed before timing either workload.
CASES = {
    "merge_streams": ((16, 1176942361268620048), (32, 4659729952249026080),
                      (64, 96096749635644480)),
    "hash_probe": ((1048576, 650970345625391104), (2097152, 2603888311816757248),
                   (4194304, 10415567105897414656)),
}


def jitter(x):
    mixed = ((x ^ (x >> 16)) * 0x85ebca6b) % (1 << 32)
    mixed = ((mixed ^ (mixed >> 13)) * 0xc2b2ae35) % (1 << 32)
    return (mixed ^ (mixed >> 16)) % 8


def merge_checksum(passes):
    # Both streams have exactly one element in [8*i, 8*i+7].
    # Sorting just two (key, stream) pairs determines their global ranks.
    first_pass = total_value = 0
    for i in range(65536):
        keys = [(8 * i + jitter(i ^ salt), tag)
                for tag, salt in enumerate((0x13579bdf, 0x2468ace0))]
        for index, (key, tag) in enumerate(sorted(keys), start=1):
            value = 2 * key + tag + 1
            first_pass += (2 * i + index) * value
            total_value += value
    offset = 131072 * passes * (passes - 1) // 2
    return (passes * first_pass + offset * total_value) % (1 << 64)


def hash_checksum(queries):
    # Invert the key generator to find the insertion number directly.
    # Lookup availability and payload never depend on table slot or probing.
    period = 65536
    inverse = pow(40503, -1, period)
    values = []
    for i in range(period):
        key = (i * 25173 + 13849) % period
        inserted_at = ((key - 44257) * inverse) % period
        values.append(1 + (key ^ (inserted_at * 97)) if inserted_at < 40000 else 0)
    cycles, rest = divmod(queries, period)
    first = sum(i * v for i, v in enumerate(values, start=1))
    result = cycles * first + period * sum(values) * cycles * (cycles - 1) // 2
    result += sum((cycles * period + i) * v for i, v in enumerate(values[:rest], start=1))
    return result % (1 << 64)


class GshareHeldoutTests(unittest.TestCase):
    def test_both_merge_streams_lead_and_tie(self):
        orders = [jitter(i ^ 0x13579bdf) - jitter(i ^ 0x2468ace0) for i in range(65536)]
        self.assertGreater(sum(x < 0 for x in orders), 16000)
        self.assertGreater(sum(x > 0 for x in orders), 16000)
        self.assertGreater(orders.count(0), 4000)
        changes = sum((a <= 0) != (b <= 0) for a, b in zip(orders, orders[1:]))
        self.assertGreater(changes, 16000)

    def test_independent_oracles_match_frozen_outputs(self):
        for name, oracle in (("merge_streams", merge_checksum), ("hash_probe", hash_checksum)):
            for steps, expected in CASES[name]:
                with self.subTest(name=name, steps=steps):
                    self.assertEqual(oracle(steps), expected)

    @unittest.skipUnless(shutil.which("g++"), "requires a C++20 compiler")
    def test_corpus_programs(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, cases in CASES.items():
                binary = Path(tmp) / name
                subprocess.run(["g++", "-std=c++20", "-O2", "-Wall", SOURCES / f"{name}.cpp",
                                "-o", binary], check=True, timeout=30)
                for steps, expected in cases:
                    with self.subTest(name=name, steps=steps):
                        run = subprocess.run([binary], input=f"{steps}\n".encode(), capture_output=True,
                                             check=True, timeout=10)
                        self.assertEqual(run.stdout, f"{expected}\n".encode())
                        self.assertEqual(run.stderr, b"")
                for bad in (b"0\n", f"{cases[-1][0] + 1}\n".encode(), b"?\n"):
                    with self.subTest(name=name, invalid=bad):
                        run = subprocess.run([binary], input=bad, capture_output=True, timeout=3)
                        self.assertEqual(run.returncode, 2)
                        self.assertEqual(run.stdout, b"")


if __name__ == "__main__":
    unittest.main()

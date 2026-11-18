from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "experiments" / "time_features" / "control.S"
WORDS = 32768


def checksum(mode):
    """Weight the two index sequences directly, independently of the ELF."""
    stride = 257 if mode & 1 else 1
    memory_sum = sum(i * (1 + (i * stride) % WORDS) for i in range(WORDS))
    if mode & 2:
        def branch_key(i):
            a = ((i ^ (i >> 7)) * 25173) % WORDS
            return ((a ^ (a >> 5)) * 13849) % WORDS
        taken_arm = [i for i in range(WORDS) if branch_key(i) >= WORDS // 2]
    else:
        taken_arm = range(WORDS // 2, WORDS)
    assert len(taken_arm) == WORDS // 2
    return f"{memory_sum + sum(taken_arm):016x}\n".encode("ascii")


@unittest.skipUnless(sys.platform == "linux" and platform.machine() == "x86_64" and
                     shutil.which("cc"), "requires a native Linux x86-64 C toolchain")
class TimeFeatureControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        build = tempfile.TemporaryDirectory()
        cls.addClassCleanup(build.cleanup)
        cls.exe = Path(build.name) / "control"
        subprocess.run(["cc", "-nostdlib", "-static", "-no-pie", "-Wl,--build-id=none",
                        SOURCE, "-o", cls.exe], check=True, timeout=30)

    def test_all_controls_have_distinct_verified_output(self):
        expected = [checksum(mode) for mode in range(4)]
        self.assertEqual(len(set(expected)), 4)
        for mode, value in enumerate(expected):
            with self.subTest(mode=mode):
                run = subprocess.run([self.exe], input=f"{mode}\n".encode("ascii"),
                                     capture_output=True, check=True, timeout=5)
                self.assertEqual(run.stdout, value)
                self.assertEqual(run.stderr, b"")

    def test_rejects_unknown_or_malformed_modes(self):
        for data in (b"4\n", b"2", b"00\n", b"0\nx"):
            with self.subTest(input=data):
                run = subprocess.run([self.exe], input=data, capture_output=True, timeout=5)
                self.assertEqual(run.returncode, 2)
                self.assertEqual(run.stdout, b"")


if __name__ == "__main__":
    unittest.main()

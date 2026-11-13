"""Exercise Java child exit/output diagnostics with deterministic native children."""
import base64
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from runtime_workloads import FIXTURES, JAVA_FLAGS


class JavaProcessFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.java = shutil.which("java")
        javac = shutil.which("javac")
        if not cls.java or not javac:
            raise unittest.SkipTest("native Java fixture requires java and javac")
        temporary = tempfile.TemporaryDirectory(prefix="durante-java-child-unit-")
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        subprocess.run([javac, "-J-Xmx128m", "-J-XX:ActiveProcessorCount=2", "-d", str(cls.root),
                        str(FIXTURES / "RuntimeWorkload.java")],
                       capture_output=True, check=True, timeout=20)

    def child(self, body):
        executable = self.root / "child"
        executable.write_text(f"#!{sys.executable}\nimport sys\nseed = int(sys.argv[-1])\n" + body)
        executable.chmod(0o700)
        return subprocess.run([self.java, *JAVA_FLAGS, "-cp", str(self.root),
                               "RuntimeWorkload", "processes", str(executable)],
                              capture_output=True, timeout=15)

    def failure(self, result, seed, exit_code, output):
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        detail = (f"child output or exit mismatch: seed={seed} exit={exit_code} "
                  f"stdout_prefix_base64={base64.b64encode(output).decode()}")
        self.assertIn(detail.encode() + b"\n", result.stderr)
        self.assertNotIn(b"child deadline", result.stderr)

    def test_success_preserves_the_two_child_exact_output(self):
        result = self.child("print(f'child:{seed * seed + 17}')\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"processes:204\n")
        self.assertEqual(result.stderr, b"")

    def test_nonzero_exit_retains_seed_and_otherwise_correct_output(self):
        for seed in (7, 11):
            with self.subTest(seed=seed):
                result = self.child("print(f'child:{seed * seed + 17}')\n"
                                    f"sys.exit(7 if seed == {seed} else 0)\n")
                self.failure(result, seed, 7, f"child:{seed * seed + 17}\n".encode())

    def test_wrong_output_keeps_exact_bytes_distinct_from_exit_failure(self):
        output = b'wrong:"\n\xff\x00'
        result = self.child(f"sys.stdout.buffer.write({output!r})\n")
        self.failure(result, 7, 0, output)

    def test_failure_output_is_bounded_to_the_original_128_byte_prefix(self):
        output = b"x" * 129
        result = self.child(f"sys.stdout.buffer.write({output!r})\n")
        self.failure(result, 7, 0, output[:128])
        encoded = re.search(rb"stdout_prefix_base64=([^\n]*)", result.stderr).group(1)
        self.assertEqual(len(base64.b64decode(encoded, validate=True)), 128)


if __name__ == "__main__":
    unittest.main()

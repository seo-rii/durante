"""Host-independent CPUID profile table and collector receipt checks."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import cpuid_profile

ROOT = Path(__file__).resolve().parent.parent


class CpuidTableTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("gcc"), "gcc is required")
    def test_c_table_unit_program(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "cpuid-unit"
            subprocess.run(["gcc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                            str(ROOT / "tests/cpuid_profile_unit.c"), "-o", str(binary)],
                           check=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "ok\n")

    def test_harness_expectations_match_the_header_constants(self):
        header = (ROOT / "client/cpuid_profile.h").read_text()
        for constant, value in (("DURANTE_CPUID_1_EDX", "0x07888111"),
                                ("DURANTE_CPUID_1_ECX_V2", "0x00982201"),
                                ("DURANTE_CPUID_EXT1_EDX", "0x20100800"),
                                ("DURANTE_CPUID_ADDRESS_SIZES", "0x00003030")):
            self.assertIn(f"#define {constant} UINT32_C({value})", header)
        self.assertIn('durante_cpuid_vendor[13] = "GenuineIntel"', header)
        leaves = cpuid_profile.expected_leaves("x86-64-v2")
        self.assertEqual(leaves["0x1.0"][2], 0x00982201)
        self.assertEqual(leaves["0x80000001.0"], [0, 0, 1, 0x20100800])
        brand = b"".join(word.to_bytes(4, "little") for leaf in
                         ("0x80000002.0", "0x80000003.0", "0x80000004.0")
                         for word in leaves[leaf])
        self.assertEqual(brand.rstrip(b"\0"), b"Durante virtual CPU x86-64-v2")


if __name__ == "__main__":
    unittest.main()

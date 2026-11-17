"""Harness logic for the exploratory runtime CPUID observations."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cpuid_runtimes


class CpuidRuntimeHarnessTests(unittest.TestCase):
    def test_group_reports_exact_spread_and_native_agreement(self):
        rows = iter([
            {"exit": 0, "stdout": "42", "instructions": None, "seconds": 0, "stderr": ""},  # native
            {"exit": 0, "stdout": "42", "instructions": 1_000_000, "seconds": 0, "stderr": ""},
            {"exit": 0, "stdout": "42", "instructions": 1_000_153, "seconds": 0, "stderr": ""},
        ])
        args = SimpleNamespace(repeats=2)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(cpuid_runtimes, "run", lambda *a, **k: next(rows)):
            result = cpuid_runtimes.group(args, {}, Path(directory), "x", "cpython", "x86-64-v2")
        self.assertTrue(result["ok"])
        self.assertEqual(result["spread_ppm"], 153)
        self.assertEqual(result["median"], 1_000_153)

    def test_group_fails_on_output_mismatch_or_missing_count(self):
        for bad in ({"stdout": "41"}, {"instructions": None}, {"exit": 1}):
            rows = iter([
                {"exit": 0, "stdout": "42", "instructions": None, "seconds": 0, "stderr": ""},
                {"exit": 0, "stdout": "42", "instructions": 10, "seconds": 0, "stderr": "", **bad},
                {"exit": 0, "stdout": "42", "instructions": 10, "seconds": 0, "stderr": ""},
            ])
            args = SimpleNamespace(repeats=2)
            with tempfile.TemporaryDirectory() as directory, mock.patch.object(cpuid_runtimes, "run", lambda *a, **k: next(rows)):
                result = cpuid_runtimes.group(args, {}, Path(directory), "x", "cpython", "x86-64-v2")
            self.assertFalse(result["ok"], bad)
            self.assertIsNone(result["spread_ppm"])

    def test_masks_only_remove_features_above_the_profiles(self):
        removed = cpuid_runtimes.GLIBC_MASK.split("=", 1)[1].split(",")
        self.assertTrue(all(item.startswith("-") for item in removed))
        for kept in ("-SSE4_2", "-SSE4_1", "-SSSE3", "-POPCNT", "-SSE3"):
            self.assertNotIn(kept, removed)
        self.assertTrue(all(word.startswith("~") for word in cpuid_runtimes.OPENSSL_MASK.split(":")))

    def test_deterministic_cpython_options_pin_the_hash_seed(self):
        # -I would ignore PYTHONHASHSEED; the deterministic options must not.
        self.assertNotIn("-I", cpuid_runtimes.DETERMINISM["cpython"])
        env = {"PATH": os.environ.get("PATH", ""), **cpuid_runtimes.DETERMINISM_ENV["cpython"]}
        command = [sys.executable, *cpuid_runtimes.DETERMINISM["cpython"], "-c",
                   "import sys; print(hash('durante'), sys.flags.hash_randomization)"]
        outputs = {subprocess.run(command, env=env, capture_output=True, text=True, check=True).stdout
                   for _ in range(2)}
        self.assertEqual(len(outputs), 1)
        self.assertTrue(outputs.pop().strip().endswith(" 0"))


if __name__ == "__main__":
    unittest.main()

"""Model fitting and instruction features of the exploratory time experiment."""
import importlib.util
import json
import os
import platform
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("time_features", ROOT / "experiments/time_features.py")
time_features = importlib.util.module_from_spec(spec)
spec.loader.exec_module(time_features)

SAMPLE = """Cache simulation results:
Core #0 (1 thread(s))
  L1I0 (size=32768, assoc=8, block=64, LRU) stats:
    Hits:                         60859067
    Misses:                            728
  L1D0 (size=32768, assoc=8, block=64, LRU) stats:
    Hits:                          1917302
    Misses:                       13300119
    Prefetch hits:                  945769
LL (size=8388608, assoc=16, block=64, LRU) stats:
    Hits:                         13297482
    Misses:                           3365
"""


class TimeFeatureTests(unittest.TestCase):
    def test_chained_lea_uses_base_and_index_without_load_latency(self):
        sdk = Path(os.environ.get("DURANTE_TEST_SDK",
            ROOT / "toolkit/sdk/DynamoRIO-Linux-11.3.0-1")).resolve()
        if platform.machine() != "x86_64" or not (sdk / "bin64/drrun").is_file():
            self.skipTest("the x86-64 DynamoRIO SDK is required")
        with tempfile.TemporaryDirectory(prefix="durante-time-features-") as tmp:
            build = Path(tmp)
            subprocess.run(["cmake", "-S", ROOT / "experiments/time_features", "-B", build,
                            "-DCMAKE_BUILD_TYPE=Release", f"-DDynamoRIO_DIR={sdk}/cmake"],
                           check=True, capture_output=True)
            subprocess.run(["cmake", "--build", build, "--target", "time_features_client"],
                           check=True, capture_output=True)
            # argc controls the iteration count. The chain goes through a base
            # operand in the first LEA and a scaled index in the second.
            source = build / "lea.S"
            source.write_text("""
.text
.global _start
_start:
    mov (%rsp), %ecx
    xor %eax, %eax
    mov $1, %edi
    jmp 1f
1:
    lea (%rax,%rdi,2), %rax
    lea (%rdi,%rax,2), %rax
    dec %ecx
    jnz 1b
    mov $60, %eax
    xor %edi, %edi
    syscall
""")
            app = build / "lea"
            subprocess.run(["cc", "-nostdlib", "-static", "-no-pie", source, "-o", app],
                           check=True, capture_output=True)
            rows = []
            for i, args in enumerate([[], ["extra"]]):
                output = build / f"report-{i}.json"
                subprocess.run([sdk / "bin64/drrun", "-quiet", "-disable_traces", "-c",
                                build / "libtime_features_client.so", "-out", output,
                                "--", app, *args], check=True, capture_output=True, timeout=10)
                rows.append(json.loads(output.read_text()))
            delta = {k: rows[1][k] - rows[0][k] for k in
                     ("instructions", "loads", "loop_recurrence_cycles_x16", "bound_cycles_x16")}
            self.assertEqual(delta, {"instructions": 4, "loads": 0,
                                     "loop_recurrence_cycles_x16": 32, "bound_cycles_x16": 32})

    def test_cache_report_parsing_keeps_demand_hits_and_misses(self):
        stats = time_features.cache_stats(SAMPLE)
        self.assertEqual(stats["l1i"], {"hits": 60859067, "misses": 728})
        self.assertEqual(stats["l1d"], {"hits": 1917302, "misses": 13300119})
        self.assertEqual(stats["ll"], {"hits": 13297482, "misses": 3365})

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "numpy is required")
    def test_nnls_recovers_nonnegative_weights_and_clamps_negative_ones(self):
        import numpy as np
        a = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0], [2.0, 1.0]])
        self.assertTrue(np.allclose(time_features.nnls(a, a @ np.array([3.0, 0.5])), [3.0, 0.5]))
        weights = time_features.nnls(a, a @ np.array([2.0, -1.0]))
        self.assertTrue((weights >= 0).all())


if __name__ == "__main__":
    unittest.main()

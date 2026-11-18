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


class TimeFeatureClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sdk = Path(os.environ.get("DURANTE_TEST_SDK",
            ROOT / "toolkit/sdk/DynamoRIO-Linux-11.3.0-1")).resolve()
        cls.sdk = sdk
        cls.drrun = sdk / "bin64/drrun"
        if platform.machine() != "x86_64" or not cls.drrun.is_file():
            raise unittest.SkipTest("the x86-64 DynamoRIO SDK is required")
        tmp = tempfile.TemporaryDirectory(prefix="durante-time-features-")
        cls.addClassCleanup(tmp.cleanup)
        cls.build = Path(tmp.name)
        subprocess.run(["cmake", "-S", ROOT / "experiments/time_features", "-B", cls.build,
                        "-DCMAKE_BUILD_TYPE=Release", f"-DDynamoRIO_DIR={sdk}/cmake"],
                       check=True, capture_output=True)
        subprocess.run(["cmake", "--build", cls.build, "--target", "time_features_client"],
                       check=True, capture_output=True)
        cls.client = cls.build / "libtime_features_client.so"

    def test_chained_lea_uses_base_and_index_without_load_latency(self):
        build = self.build
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
            subprocess.run([self.drrun, "-quiet", "-disable_traces", "-c",
                            self.client, "-out", output,
                            "--", app, *args], check=True, capture_output=True, timeout=10)
            rows.append(json.loads(output.read_text()))
        delta = {k: rows[1][k] - rows[0][k] for k in
                 ("instructions", "loads", "loop_recurrence_cycles_x16", "bound_cycles_x16")}
        self.assertEqual(delta, {"instructions": 4, "loads": 0,
                                 "loop_recurrence_cycles_x16": 32, "bound_cycles_x16": 32})

    def test_load_pattern_uses_per_pc_starting_lines_and_preserves_app_state(self):
        # For the indexed MOV the accessed offsets are 0, 63, 64, 0, 192.
        # The eight-byte access at 63 crosses a line but starts on line zero.
        # Each extension opcode is a separate, fixed-address load site.
        source = self.build / "patterns.S"
        source.write_text("""
.data
.balign 64
buf: .fill 256,1,1
.text
.global _start
_start:
    mov %rsp, %rbp
    mov $2, %r10
    lea buf-8(%rip), %r12
    stc
    call sample
    lea 63(%r12), %r12
    call sample
    lea 1(%r12), %r12
    call sample
    lea -64(%r12), %r12
    call sample
    lea 192(%r12), %r12
    call sample
    jnc fail
    movabs $0x0101010101010101, %r9
    cmp %r9, %rax
    jne fail
    cmp $1, %ecx
    jne fail
    cmp $257, %edx
    jne fail
    cmp $16843009, %r8
    jne fail
    xor %edi, %edi
    jmp done
fail:
    mov $1, %edi
done:
    mov $60, %eax
    syscall
sample:
    movq 4(%r12,%r10,2), %rax
    movzbl buf(%rip), %ecx
    movswl buf(%rip), %edx
    movslq buf(%rip), %r8
    movq (%rsp), %r11
    movq (%rbp), %rdi
    movdqu buf(%rip), %xmm0
    prefetchnta buf(%rip)
    ret
""")
        app = self.build / "patterns"
        subprocess.run(["cc", "-nostdlib", "-static", "-no-pie", source, "-o", app],
                       check=True, capture_output=True)
        reports = []
        for i, flags in enumerate([[], ["-load_pattern"], ["-load_pattern"]]):
            output = self.build / f"patterns-{i}.json"
            subprocess.run([self.drrun, "-quiet", "-disable_traces", "-c", self.client,
                            "-out", output, "-bpred", *flags, "--", app],
                           check=True, capture_output=True, timeout=10)
            reports.append(json.loads(output.read_text()))
        self.assertNotIn("load_pattern", reports[0])
        for report in reports[1:]:
            self.assertEqual(report.pop("load_pattern"), {
                "version": 1, "first": 4, "same_line": 13,
                "adjacent_line": 2, "nonadjacent_line": 1,
            })
            self.assertEqual(report, reports[0])

    def test_load_pattern_with_judge_cpuid_emulation(self):
        # This program checks the actual CPUID and XGETBV results before
        # running a three-iteration load loop and a four-byte REP MOVSB.
        from test_load_probe import ASSEMBLY

        installed = os.environ.get("DURANTE_TEST_BUNDLE")
        if installed:
            drrun = Path(installed) / "dynamorio/bin64/drrun"
            meter = Path(installed) / "lib/libdurante.so"
        else:
            if not (self.sdk / ".durante-runtime.json").is_file():
                self.skipTest("requires a patched SDK or DURANTE_TEST_BUNDLE")
            drrun = self.drrun
            dest = self.build / "meter"
            subprocess.run(["cmake", "-S", ROOT / "client", "-B", dest, "-DCMAKE_BUILD_TYPE=Release",
                            f"-DDynamoRIO_DIR={self.sdk}/cmake"], check=True, capture_output=True, timeout=60)
            subprocess.run(["cmake", "--build", dest, "--target", "durante"], check=True,
                           capture_output=True, timeout=60)
            meter = dest / "libdurante.so"
        app = self.build / "profile-fixture"
        subprocess.run(["cc", "-nostdlib", "-static", "-no-pie", "-x", "assembler", "-", "-o", app],
                       input=ASSEMBLY, text=True, check=True, timeout=30)
        records = self.build / "profile-records"
        records.mkdir()
        out = self.build / "profile-pattern.json"
        result = subprocess.run([drrun, "-quiet", "-client", meter,
                                 "00000000", f'-outdir "{records}" -rep_bytes -cpuid_profile x86-64-v2 -latency_bound',
                                 "-client", self.client, "00000001", f'-out "{out}" -bpred -load_pattern',
                                 "-disable_traces", "--", app], capture_output=True, timeout=30)
        self.assertEqual((result.returncode, result.stderr), (0, b""))
        log, = records.iterdir()
        start, end = map(json.loads, log.read_text().splitlines())
        raw = json.loads(out.read_text())
        self.assertEqual(start["cpuid_profile"], "x86-64-v2")
        self.assertEqual((end["instructions"], end["rep_bytes"]), (33, 4))
        self.assertEqual((raw["instructions"], raw["loads"]), (33, 7))
        self.assertEqual(raw["load_pattern"], {"version": 1, "first": 1, "same_line": 2,
                                               "adjacent_line": 0, "nonadjacent_line": 0})

    def test_load_pattern_rejects_a_second_application_thread(self):
        source = self.build / "thread.c"
        source.write_text("""
#include <pthread.h>
static void *worker(void *arg) { return arg; }
int main(void) {
    pthread_t t;
    return pthread_create(&t, 0, worker, 0) || pthread_join(t, 0);
}
""")
        app = self.build / "thread"
        subprocess.run(["cc", "-static", "-no-pie", "-pthread", source, "-o", app],
                       check=True, capture_output=True)
        output = self.build / "thread.json"
        result = subprocess.run([self.drrun, "-quiet", "-disable_traces", "-c", self.client,
                                 "-out", output, "-load_pattern", "--", app],
                                capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"requires a single application thread", result.stderr)
        self.assertFalse(output.exists())


class TimeFeatureTests(unittest.TestCase):
    def test_cache_report_parsing_keeps_demand_hits_and_misses(self):
        stats = time_features.cache_stats(SAMPLE)
        self.assertEqual(stats["l1i"], {"hits": 60859067, "misses": 728})
        self.assertEqual(stats["l1d"], {"hits": 1917302, "misses": 13300119})
        self.assertEqual(stats["ll"], {"hits": 13297482, "misses": 3365})

    def test_cache_counts_must_come_from_the_named_section(self):
        truncated = SAMPLE.replace("    Hits:                          1917302\n", "").replace(
            "    Misses:                       13300119\n", "")
        with self.assertRaisesRegex(ValueError, "incomplete L1D0"):
            time_features.cache_stats(truncated)

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "numpy is required")
    def test_nnls_recovers_nonnegative_weights_and_clamps_negative_ones(self):
        import numpy as np
        a = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0], [2.0, 1.0]])
        self.assertTrue(np.allclose(time_features.nnls(a, a @ np.array([3.0, 0.5])), [3.0, 0.5]))
        weights = time_features.nnls(a, a @ np.array([2.0, -1.0]))
        self.assertTrue((weights >= 0).all())


if __name__ == "__main__":
    unittest.main()

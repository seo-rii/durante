import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from shape_calibration import temporal


def report(n, exact=0, radius=0):
    windows = (n + temporal.PERIOD - 1 - temporal.WINDOW) // temporal.PERIOD
    counters = dict(loads=n, windows=windows, exact_hits=exact, radius_hits=radius)
    return dict(schema=1, pid=1234, threads=1, period=8191, line_bytes=64, window=32, radius_lines=64,
                **counters, sites=[dict(offset=20, **counters)] if n else [])


def parsed(receipt):
    raw = json.dumps(receipt).encode()
    return {"observation": {"elf_sha256": "1" * 64, "probe_sha256": temporal.PROBE_SHA, "sample_sha256": temporal.sha(raw)},
            "sites": temporal.sample_sites(raw, 1234)}


class TemporalWindows(unittest.TestCase):
    def test_first_reference_and_last_follower_boundaries(self):
        for n, windows in ((0, 0), (1, 0), (32, 0), (33, 1), (8191, 1), (8192, 1), (8223, 1), (8224, 2)):
            r = report(n)
            self.assertEqual(sum(s["windows"] for s in parsed(r)["sites"]), windows)
            r["windows"] += 1
            with self.assertRaises(ValueError):
                parsed(r)
        for invalid in (report(33, 1, 0), report(33, 1, 2)):
            with self.assertRaises(ValueError):
                parsed(invalid)

    def test_material_confidence_and_strict_drift_threshold(self):
        n = 8191 * 64
        base = parsed(report(n, 8, 16))
        self.assertEqual(temporal.compare(base, parsed(report(n, 16, 24)))["status"], "reuse")
        self.assertEqual(temporal.compare(base, parsed(report(n, 17, 24)))["reasons"], ["exact_hits_changed"])
        self.assertEqual(temporal.compare(base, parsed(report(n, 8, 25)))["reasons"], ["radius_hits_changed"])
        self.assertEqual(temporal.compare(base, parsed(report(n - 8191, 8, 16)))["status"], "needs_native")
        self.assertEqual(temporal.compare(parsed(report(0)), parsed(report(0)))["status"], "needs_native")

    @unittest.skipUnless(os.environ.get("DURANTE_TEMPORAL_PROBE") and os.environ.get("DURANTE_GUARDED_PAYLOAD"),
                         "set the registered v11 payload and temporal sidecar for the instrumented assembly oracle")
    def test_actual_single_site_trace_with_partial_final_window(self):
        # The sequential groups have no return, a return at radius 64, and an exact return,
        # respectively. The fourth group stops after 31 followers. Only the loop MOV reads memory.
        source = """
.global _start
.text
_start:
    leaq buf(%rip), %rdi
    movl $24605, %ecx
    xorl %r8d, %r8d
    xorl %r9d, %r9d
loop:
    movl $8192, %edx
    testl %r9d, %r9d
    jz zero
    cmpl $32, %r9d
    jne read
    cmpl $1, %r8d
    jne exact
    movl $4096, %edx
    jmp read
exact:
    cmpl $2, %r8d
    jne read
zero:
    xorl %edx, %edx
read:
    movl (%rdi,%rdx), %eax
    incl %r9d
    cmpl $8191, %r9d
    jne next
    xorl %r9d, %r9d
    incl %r8d
next:
    decl %ecx
    jne loop
    movl $60, %eax
    xorl %edi, %edi
    syscall
.bss
.balign 64
buf:
    .skip 8196
"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "control.S").write_text(source)
            elf = root / "control"
            subprocess.run(["cc", "-nostdlib", "-static", "-Wl,--build-id=none", root / "control.S", "-o", elf], check=True)
            stdin = root / "input"
            stdin.touch()
            out = root / "capture"
            result = temporal.capture(os.environ["DURANTE_GUARDED_PAYLOAD"], os.environ["DURANTE_TEMPORAL_PROBE"],
                                      elf, stdin, out)
            sites = temporal.inspect_run(out, result["elf_sha256"], result["stdin_sha256"], temporal.sha(b""))["sites"]
            self.assertEqual(len(sites), 1)
            self.assertEqual([sites[0][k] for k in temporal.FIELDS], [24605, 3, 1, 2])
            (out / "stdout").write_bytes(b"wrong")
            with self.assertRaisesRegex(ValueError, "mismatch"):
                temporal.inspect_run(out, result["elf_sha256"], result["stdin_sha256"], temporal.sha(b""))

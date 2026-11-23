import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from shape_calibration import combined


class CombinedSamplers(unittest.TestCase):
    def test_marker_rejects_relabeling_a_single_combined_process(self):
        expected = dict(schema=1, launches=1, pid=81, cpu_ns=100, clock_sha256="a" * 64)
        with tempfile.TemporaryDirectory() as temp, patch.object(combined, "_receipts", return_value=(expected, "profile")):
            root = Path(temp)
            (root / "combined.json").write_text(json.dumps(expected))
            self.assertEqual(combined.inspect_temporal(root, "elf", "stdin", "stdout"), "profile")
            for key, wrong in (("launches", True), ("launches", 2), ("pid", 82), ("clock_sha256", "b" * 64)):
                with self.subTest(key=key, wrong=wrong):
                    (root / "combined.json").write_text(json.dumps({**expected, key: wrong}))
                    with self.assertRaisesRegex(ValueError, "incorrect combined receipt"):
                        combined.inspect_temporal(root, "elf", "stdin", "stdout")

    @unittest.skipUnless(os.environ.get("DURANTE_TEMPORAL_PROBE") and os.environ.get("DURANTE_GUARDED_PAYLOAD"),
                         "set registered v11 payload and temporal sidecar to run both samplers")
    def test_actual_v11_with_two_sidecars_and_one_root(self):
        # The only eligible load reads the same address 16,416 times.
        code = """
.global _start
.text
_start:
    leaq value(%rip), %rdi
    movl $16416, %ecx
loop:
    movl (%rdi), %eax
    decl %ecx
    jne loop
    movl $60, %eax
    xorl %edi, %edi
    syscall
.data
.balign 64
value: .long 42
"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "control.S").write_text(code)
            elf = root / "control"
            subprocess.run(["cc", "-nostdlib", "-static", "-Wl,--build-id=none", root / "control.S", "-o", elf], check=True)
            stdin = root / "input"
            stdin.touch()
            run = root / "run"
            row, extra = combined.capture(os.environ["DURANTE_GUARDED_PAYLOAD"], os.environ["DURANTE_TEMPORAL_PROBE"],
                                          elf, stdin, run)
            source = (run, extra["elf_sha256"], extra["stdin_sha256"], extra["stdout_sha256"])
            temporal = combined.inspect_temporal(*source)
            spatial = combined.shape.inspect_run(run / "full", extra["elf_sha256"])
            self.assertEqual((row["launcher_pid"], extra["cpu_ns"]), (extra["pid"], row["cpu_us"] * 1000))
            self.assertEqual(len(list(run.rglob("clock.bin"))), 1)
            self.assertEqual(len(list(run.rglob("process-*.jsonl"))), 1)
            self.assertEqual(len(list(run.rglob("stdout"))), 1)
            first, = spatial["sites"]
            second, = temporal["sites"]
            self.assertEqual(first["offset"], second["offset"])
            self.assertEqual((first["loads"], first["near_pairs"], first["far_pairs"]), (16416, 17, 0))
            self.assertEqual((second["loads"], second["windows"], second["exact_hits"], second["radius_hits"]),
                             (16416, 3, 3, 3))
            # Both metadata views refer to one actual child. Changing an otherwise consistent
            # temporal user/system/total tuple cannot replace its wait4 sample.
            path = run / "full/full/temporal-result.json"
            metadata = json.loads(path.read_bytes())
            metadata["user_us"] += 1
            metadata["cpu_ns"] += 1000
            path.write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError, "child CPU"):
                combined.inspect_temporal(*source)

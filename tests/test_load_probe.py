import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ASSEMBLY = """
.global _start
.text
_start:
  mov $1, %eax
  xor %ecx, %ecx
  cpuid
  cmp $0x00982201, %ecx
  jne bad
  xor %ecx, %ecx
  xgetbv
  cmp $3, %eax
  jne bad
  lea word(%rip), %rsi
  mov $3, %ecx
again:
  mov (%rsi), %eax
  addl $1, (%rsi)
  lea (%rsi), %rsi
  dec %ecx
  jne again
  mov %eax, (%rsi)
  lea dest(%rip), %rdi
  mov $4, %ecx
  rep movsb
  mov $60, %eax
  xor %edi, %edi
  syscall
bad:
  mov $60, %eax
  mov $1, %edi
  syscall
.data
word: .long 42
dest: .long 0
.section .note.GNU-stack,"",@progbits
"""


class LoadProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sdk = Path(os.environ.get("DURANTE_TEST_SDK",
            ROOT / "toolkit/sdk/DynamoRIO-Linux-11.3.0-1")).resolve()
        cls.drrun = sdk / "bin64/drrun"
        if platform.machine() != "x86_64" or not (sdk / ".durante-runtime.json").is_file():
            raise unittest.SkipTest("requires a prepared x86-64 Durante SDK")
        tmp = tempfile.TemporaryDirectory(prefix="durante-load-probe-")
        cls.addClassCleanup(tmp.cleanup)
        cls.directory = Path(tmp.name)

        def build(project, target):
            dest = cls.directory / target
            subprocess.run(["cmake", "-S", ROOT / project, "-B", dest,
                            "-DCMAKE_BUILD_TYPE=Release", f"-DDynamoRIO_DIR={sdk}/cmake"],
                           check=True, capture_output=True, timeout=60)
            subprocess.run(["cmake", "--build", dest, "--target", target],
                           check=True, capture_output=True, timeout=60)
            return dest / f"lib{target}.so"

        cls.production = build("client", "durante")
        cls.probe = build("experiments/load_probe", "load_probe")
        cls.program = cls.directory / "fixture"
        subprocess.run(["cc", "-x", "assembler", "-nostdlib", "-static", "-no-pie",
                        "-", "-o", cls.program], input=ASSEMBLY, text=True, check=True, timeout=30)

    def launch(self, program, probe, out):
        records = out.parent / "records"
        records.mkdir()
        command = [self.drrun, "-quiet", "-client", self.production, "00000000",
                   f'-outdir "{records}" -rep_bytes -cpuid_profile x86-64-v2 -latency_bound']
        if probe:
            command += ["-client", self.probe, "00000001", f'-out "{out}"']
        command += ["-follow_children", "-disable_traces", "-vm_size", "32M",
                    "-vmheap_size", "32M", "--", program]
        result = subprocess.run(command, capture_output=True, timeout=30)
        return result, records

    def test_hand_count_with_and_without_probe(self):
        # 11 setup instructions (including CPUID/XGETBV), 3 * 5 loop
        # instructions, 7 tail instructions. Each iteration has one explicit
        # load and one read-modify-write. REP MOVSB adds one read, independent
        # of its four bytes. LEA and the store add zero reads.
        for enabled in (False, True):
            with self.subTest(probe=enabled), tempfile.TemporaryDirectory(dir=self.directory) as tmp:
                out = Path(tmp) / "loads.json"
                result, records = self.launch(self.program, enabled, out)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, b"")
                log, = records.iterdir()
                start, end = map(json.loads, log.read_text().splitlines())
                self.assertEqual((start["event"], start["cpuid_profile"]), ("start", "x86-64-v2"))
                self.assertEqual((end["event"], end["instructions"], end["rep_bytes"]),
                                 ("end", 33, 4))
                self.assertGreater(end["latency_bound_x16"], 0)
                if enabled:
                    self.assertEqual(json.loads(out.read_text()),
                                     {"schema": 1, "pid": end["pid"], "threads": 1,
                                      "instructions": 33, "load_instructions": 7})
                else:
                    self.assertFalse(out.exists())

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory(dir=self.directory) as tmp:
            out = Path(tmp) / "loads.json"
            out.write_text("keep")
            result, _ = self.launch(self.program, True, out)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(out.read_text(), "keep")
            self.assertIn(b"cannot create a fresh report", result.stderr)

    def test_extra_thread_rejected(self):
        with tempfile.TemporaryDirectory(dir=self.directory) as tmp:
            out = Path(tmp) / "loads.json"
            program = Path(tmp) / "thread"
            source = """
#include <pthread.h>
static void *work(void *arg) { return arg; }
int main(void) {
  pthread_t t;
  return pthread_create(&t, 0, work, 0) || pthread_join(t, 0);
}
"""
            subprocess.run(["cc", "-x", "c", "-static", "-pthread", "-", "-o", program],
                           input=source, text=True, check=True, timeout=30)
            result, _ = self.launch(program, True, out)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"multiple threads", result.stderr)
            self.assertEqual(out.read_bytes(), b"")


if __name__ == "__main__":
    unittest.main()

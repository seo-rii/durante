"""Decode authored x86 instructions with the actual SDK and classify their IR.

No instruction under test is executed, so AVX/AVX-512 coverage does not depend
on host CPU support. The runtime harness separately checks pre-execution stops.
"""
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include "dr_api.h"
#include "cpu_policy.h"
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    FILE *input = fopen(argv[1], "rb");
    if (!input) return 2;
    byte bytes[16];
    size_t length = fread(bytes, 1, sizeof(bytes), input);
    fclose(input);
    if (length == 0 || length > 15) return 2;
    void *context = dr_standalone_init();
    instr_t instruction;
    instr_init(context, &instruction);
    byte *next = decode(context, bytes, &instruction);
    if (next != NULL && next != bytes + length) return 3;
    printf("{\"opcode\":\"%s\",\"none\":%d,\"avx\":%d,\"simd\":%d,\"both\":%d}\n",
        decode_opcode_name(instr_get_opcode(&instruction)),
        cpu_policy_classify(&instruction, false, false),
        cpu_policy_classify(&instruction, true, false),
        cpu_policy_classify(&instruction, false, true),
        cpu_policy_classify(&instruction, true, true));
    instr_free(context, &instruction);
    dr_standalone_exit();
    return 0;
}
'''


class NativeCPUClassifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sdk = Path(os.environ.get("DURANTE_TEST_SDK",
            ROOT / "toolkit/sdk/DynamoRIO-Linux-11.3.0-1")).resolve()
        if (platform.system() != "Linux" or platform.machine() != "x86_64"
                or not (cls.sdk / "include/dr_api.h").is_file()
                or not (cls.sdk / "lib64/release/libdynamorio.so").is_file()
                or not shutil.which("gcc") or not shutil.which("objcopy")):
            raise unittest.SkipTest("SDK, Linux x86-64, gcc and objcopy required")
        cls.temp = tempfile.TemporaryDirectory(prefix="durante-native-policy-decode-")
        cls.root = Path(cls.temp.name)
        cls.harness = cls.root / "decode-policy"
        source = cls.root / "decode-policy.c"
        source.write_text(HARNESS)
        subprocess.run(["gcc", "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror",
            "-DX86_64", "-DLINUX", "-I" + str(cls.sdk / "include"),
            "-I" + str(ROOT / "client"), str(source),
            "-L" + str(cls.sdk / "lib64/release"),
            "-Wl,-rpath," + str(cls.sdk / "lib64/release"), "-ldynamorio",
            "-o", str(cls.harness)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def check_instructions(self, instructions, expected):
        for instruction in instructions:
            with self.subTest(instruction=instruction):
                assembly = self.root / "instruction.S"
                obj, raw = self.root / "instruction.o", self.root / "instruction.bin"
                assembly.write_text(".text\n" + instruction + "\n")
                subprocess.run(["gcc", "-c", str(assembly), "-o", str(obj)],
                               check=True, capture_output=True)
                subprocess.run(["objcopy", "--dump-section", ".text=" + str(raw), str(obj)],
                               check=True, capture_output=True)
                result = subprocess.run([str(self.harness), str(raw)], check=True,
                                        capture_output=True, text=True, timeout=5)
                decoded = json.loads(result.stdout)
                self.assertEqual([decoded[key] for key in ("none", "avx", "simd", "both")],
                                 expected, decoded)

    def test_scalar_abi_moves_and_zeroing_remain_available(self):
        self.check_instructions([
            "nop", "add %rax,%rbx", "crc32 %eax,%ebx", "andn %eax,%ebx,%ecx",
            "pdep %eax,%ebx,%ecx", "movd %eax,%xmm0", "movq %rax,%xmm0",
            "movd %xmm0,%eax", "movdqu %xmm0,(%rdi)", "movaps (%rdi),%xmm1",
            "movss %xmm0,%xmm1", "movsd %xmm0,%xmm1", "addss %xmm0,%xmm1",
            "addsd %xmm0,%xmm1", "subsd %xmm0,%xmm1", "mulsd %xmm0,%xmm1",
            "divss %xmm0,%xmm1", "sqrtsd %xmm0,%xmm1", "roundsd $0,%xmm0,%xmm1",
            "ucomisd %xmm0,%xmm1", "cvtsi2sd %rax,%xmm0", "cvttsd2si %xmm0,%rax",
            "cmpeqss %xmm0,%xmm1", "cmpltsd %xmm0,%xmm1",
            "pxor %xmm0,%xmm0", "xorps %xmm1,%xmm1", "xorpd %xmm15,%xmm15",
            "ldmxcsr (%rax)", "stmxcsr (%rax)", "verr %ax", "verw %ax",
            "vmcall", "vmfunc", "vmrun %rax", "vmptrld (%rax)",
        ], [0, 0, 0, 0])

    def test_packed_sse_and_mmx_are_simd(self):
        self.check_instructions([
            "addps %xmm0,%xmm1", "paddq %xmm0,%xmm1", "andpd %xmm0,%xmm1",
            "pxor %xmm0,%xmm1", "xorps (%rdi),%xmm0", "xorpd %xmm0,%xmm1",
            "movq %rax,%mm0", "movd %eax,%mm0", "pxor %mm0,%mm0", "emms", "femms",
            "maskmovdqu %xmm0,%xmm1", "pblendw $1,%xmm0,%xmm1", "pinsrd $0,%eax,%xmm0",
            "aesenc %xmm0,%xmm1", "pshufd $0,%xmm0,%xmm1",
        ], [0, 0, 2, 2])

    def test_avx_scalar_vector_and_operand_free_encodings(self):
        self.check_instructions([
            "vzeroupper", "vzeroall", "vaddsd %xmm0,%xmm1,%xmm2",
            "vmovd %eax,%xmm0", "vmovdqu %xmm0,(%rdi)", "vpaddq %ymm0,%ymm1,%ymm2",
            "vfmadd132sd %xmm0,%xmm1,%xmm2", "vpxord %zmm0,%zmm1,%zmm2",
            "kmovw %eax,%k1", "kortestw %k1,%k2", "vfrczss %xmm0,%xmm1",
            ".byte 0xc5,0xf8,0x77",
        ], [0, 1, 2, 1])

    def test_pinned_decoder_rejects_amx_instead_of_allowing_unknown_ir(self):
        self.check_instructions([
            "ldtilecfg (%rax)", "sttilecfg (%rax)", "tilerelease", "tilezero %tmm0",
            "tdpbf16ps %tmm0,%tmm1,%tmm2",
        ], [0, 3, 3, 3])


if __name__ == "__main__":
    unittest.main()

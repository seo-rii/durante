/* Freestanding fixed workloads: each executed instruction is counted once.
 * Scalar/SSE2/AVX2/AVX512 compute identical 16 x u32 and 8 x f64 sums.
 * AVX512 additionally checks alternating masked merge and zero arithmetic.
 * Loads and masks remain live across explicit BB boundaries. EFLAGS remain
 * live across all SIMD operations; scalar integer ADD intentionally changes ZF.
 * No libc, dynamic loader, loops, padding or input-dependent paths execute.
 */
#ifndef SIMD_LEVEL
#error SIMD_LEVEL must be 0 (scalar), 1 (SSE2), 2 (AVX2) or 3 (AVX512F)
#endif
#ifndef EXIT_CODE
#define EXIT_CODE 0
#endif
#if SIMD_LEVEL == 3
#define FLAGS_OFFSET 384
#else
#define FLAGS_OFFSET 128
#endif
#define OUTPUT_BYTES (FLAGS_OFFSET + 2)

.global _start
.text
_start:
    mov $0, %eax
    cmp $0, %eax
    jmp .Lbody
.Lbody:
#if SIMD_LEVEL == 0
    movl ints_a(%rip), %eax
    jmp .Lint_scalar
.Lint_scalar:
    addl ints_b(%rip), %eax
    movl %eax, output(%rip)
    .set off, 4
    .rept 15
    movl ints_a+off(%rip), %eax
    addl ints_b+off(%rip), %eax
    movl %eax, output+off(%rip)
    .set off, off+4
    .endr
    movsd fp_a(%rip), %xmm0
    jmp .Lfp_scalar
.Lfp_scalar:
    addsd fp_b(%rip), %xmm0
    movsd %xmm0, output+64(%rip)
    .set off, 8
    .rept 7
    movsd fp_a+off(%rip), %xmm0
    addsd fp_b+off(%rip), %xmm0
    movsd %xmm0, output+64+off(%rip)
    .set off, off+8
    .endr
#elif SIMD_LEVEL == 1
    movdqu ints_a(%rip), %xmm3
    jmp .Lint_sse2
.Lint_sse2:
    paddd ints_b(%rip), %xmm3
    movdqu %xmm3, output(%rip)
    .set off, 16
    .rept 3
    movdqu ints_a+off(%rip), %xmm3
    paddd ints_b+off(%rip), %xmm3
    movdqu %xmm3, output+off(%rip)
    .set off, off+16
    .endr
    movupd fp_a(%rip), %xmm0
    jmp .Lfp_sse2
.Lfp_sse2:
    addpd fp_b(%rip), %xmm0
    movupd %xmm0, output+64(%rip)
    .set off, 16
    .rept 3
    movupd fp_a+off(%rip), %xmm0
    addpd fp_b+off(%rip), %xmm0
    movupd %xmm0, output+64+off(%rip)
    .set off, off+16
    .endr
#elif SIMD_LEVEL == 2
    vmovdqu ints_a(%rip), %ymm3
    jmp .Lint_avx2
.Lint_avx2:
    vpaddd ints_b(%rip), %ymm3, %ymm5
    vmovdqu %ymm5, output(%rip)
    vmovdqu ints_a+32(%rip), %ymm3
    vpaddd ints_b+32(%rip), %ymm3, %ymm5
    vmovdqu %ymm5, output+32(%rip)
    vmovupd fp_a(%rip), %ymm0
    jmp .Lfp_avx2
.Lfp_avx2:
    vaddpd fp_b(%rip), %ymm0, %ymm2
    vmovupd %ymm2, output+64(%rip)
    vmovupd fp_a+32(%rip), %ymm0
    vaddpd fp_b+32(%rip), %ymm0, %ymm2
    vmovupd %ymm2, output+96(%rip)
    vzeroupper
#elif SIMD_LEVEL == 3
    vmovdqu32 ints_a(%rip), %zmm3
    jmp .Lint_avx512
.Lint_avx512:
    vmovdqu32 ints_b(%rip), %zmm4
    vpaddd %zmm4, %zmm3, %zmm5
    vmovdqu32 %zmm5, output(%rip)
    vmovupd fp_a(%rip), %zmm0
    jmp .Lfp_avx512
.Lfp_avx512:
    vmovupd fp_b(%rip), %zmm1
    vaddpd %zmm1, %zmm0, %zmm2
    vmovupd %zmm2, output+64(%rip)
    mov $0x5555, %eax
    kmovw %eax, %k1
    jmp .Lmask_avx512
.Lmask_avx512:
    vpbroadcastd int_sentinel(%rip), %zmm5
    vpaddd %zmm4, %zmm3, %zmm5{%k1}
    vmovdqu32 %zmm5, output+128(%rip)
    vpaddd %zmm4, %zmm3, %zmm5{%k1}{z}
    vmovdqu32 %zmm5, output+192(%rip)
    vbroadcastsd fp_sentinel(%rip), %zmm2
    vaddpd %zmm1, %zmm0, %zmm2{%k1}
    vmovupd %zmm2, output+256(%rip)
    vaddpd %zmm1, %zmm0, %zmm2{%k1}{z}
    vmovupd %zmm2, output+320(%rip)
    vzeroupper
#else
#error invalid SIMD_LEVEL
#endif
    setz output+FLAGS_OFFSET(%rip)
    setnz output+FLAGS_OFFSET+1(%rip)
    mov $1, %eax
    mov $1, %edi
    lea output(%rip), %rsi
    mov $OUTPUT_BYTES, %edx
    syscall
    mov $60, %eax
    mov $EXIT_CODE, %edi
    syscall

.section .rodata
.p2align 6
ints_a: .long 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16
ints_b: .long 100,101,102,103,104,105,106,107,108,109,110,111,112,113,114,115
fp_a: .double 1,2,3,4,5,6,7,8
fp_b: .double 0.5,1,1.5,2,2.5,3,3.5,4
int_sentinel: .long 0xffffffff
fp_sentinel: .double -1
.bss
.p2align 6
output: .skip OUTPUT_BYTES
.section .note.GNU-stack,"",@progbits

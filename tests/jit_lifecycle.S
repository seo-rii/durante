/* MODE 0: RW -> RX -> RW -> RX. MODE 1: unmap/remap the owned page.
 * MODE 2: rewrite an RWX page after its first code version was executed.
 * Each loop calls ten times; only the second generated body has EXTRA_NOPS.
 * All instructions before failure execute once except the five-instruction
 * loop bodies, which execute ten times. Generated bodies are in .rodata so an
 * independent objdump count of the static success path excludes their bytes.
 */
#ifndef MODE
#define MODE 0
#endif
#ifndef EXTRA_NOPS
#define EXTRA_NOPS 0
#endif
.global _start, failure
.text
_start:
    mov $9, %eax
    xor %edi, %edi
    mov $4096, %esi
#if MODE == 2
    mov $7, %edx
#else
    mov $3, %edx
#endif
    mov $34, %r10d
    mov $-1, %r8
    xor %r9d, %r9d
    syscall
    test %rax, %rax
    js failure
    mov %rax, %r12
    mov %r12, %rdi
    lea code_a(%rip), %rsi
    mov $(code_a_end-code_a), %ecx
    rep movsb
#if MODE != 2
    mov $10, %eax
    mov %r12, %rdi
    mov $4096, %esi
    mov $5, %edx
    syscall
    test %eax, %eax
    jne failure
#endif
    mov $10, %ebx
first_loop:
    call *%r12
    cmp $42, %eax
    jne failure
    dec %ebx
    jnz first_loop

#if MODE == 0
    mov $10, %eax
    mov %r12, %rdi
    mov $4096, %esi
    mov $3, %edx
    syscall
    test %eax, %eax
    jne failure
#elif MODE == 1
    mov $11, %eax
    mov %r12, %rdi
    mov $4096, %esi
    syscall
    test %eax, %eax
    jne failure
    mov $9, %eax
    mov %r12, %rdi
    mov $4096, %esi
    mov $3, %edx
    mov $50, %r10d
    mov $-1, %r8
    xor %r9d, %r9d
    syscall
    cmp %r12, %rax
    jne failure
#endif
    mov %r12, %rdi
    lea code_b(%rip), %rsi
    mov $(code_b_end-code_b), %ecx
    rep movsb
#if MODE != 2
    mov $10, %eax
    mov %r12, %rdi
    mov $4096, %esi
    mov $5, %edx
    syscall
    test %eax, %eax
    jne failure
#endif
    mov $10, %ebx
second_loop:
    call *%r12
    cmp $43, %eax
    jne failure
    dec %ebx
    jnz second_loop
    mov $1, %eax
    mov $1, %edi
    lea message(%rip), %rsi
    mov $(message_end-message), %edx
    syscall
    mov $60, %eax
    xor %edi, %edi
    syscall
failure:
    mov $60, %eax
    mov $91, %edi
    syscall
.section .rodata
code_a:
    mov $42, %eax
    ret
code_a_end:
code_b:
    .rept EXTRA_NOPS
    nop
    .endr
    mov $43, %eax
    ret
code_b_end:
message: .ascii "jit-lifecycle-ok\n"
message_end:
.section .note.GNU-stack,"",@progbits

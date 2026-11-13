/* Fixed Linux x86-64 success paths. No libc, threads or child processes.
 * CASE 0: scalar store to other cached code; 1: patch an already executed
 * instruction in the current BB; 2/3: partial forward/backward REP MOVSB;
 * 4: partial STOSB to data sharing a cached code page; 5: twelve rewrites.
 * The two boundary pages have different permissions, so four REP iterations
 * complete on the RW page before the store reaches the cached RWX page.
 * Generated functions live in .rodata; the independent text disassembly
 * oracle adds their fixed execution counts separately.
 */
#ifndef CASE
#define CASE 0
#endif
.global _start, failure, rewrite_loop, rewrite_loop_end
.text
_start:
    mov $9, %eax
    xor %edi, %edi
#if CASE >= 2 && CASE <= 4
    mov $8192, %esi
    mov $3, %edx
#else
    mov $4096, %esi
    mov $7, %edx
#endif
    mov $34, %r10d
    mov $-1, %r8
    xor %r9d, %r9d
    syscall
    test %rax, %rax
    js failure
    mov %rax, %r15
#if CASE >= 2 && CASE <= 4
    mov $10, %eax
    mov %r15, %rdi
#if CASE != 3
    add $4096, %rdi
#endif
    mov $4096, %esi
    mov $7, %edx
    syscall
    test %eax, %eax
    jne failure
#endif
#if CASE == 2
    lea 4096(%r15), %r12
#elif CASE == 3
    lea 4090(%r15), %r12
#elif CASE == 4
    lea 4160(%r15), %r12
#else
    mov %r15, %r12
#endif
    mov %r12, %rdi
#if CASE == 1
    lea code_self(%rip), %rsi
    mov $(code_self_end-code_self), %ecx
#else
    lea code_a(%rip), %rsi
    mov $(code_a_end-code_a), %ecx
#endif
    rep movsb
    call *%r12
    cmp $42, %eax
    jne failure

#if CASE == 0
    /* Both sides of the faulting scalar write have observable work. */
    mov $0x345, %r14d
    add $7, %r14d
    movb $43, 1(%r12)
    add $11, %r14d
    cmp $0x357, %r14d
    jne failure
    call *%r12
    cmp $43, %eax
    jne failure
#elif CASE == 1
    /* Serialize before executing the modified, earlier instruction again.
     * The first call must retain EAX=42 across its own mid-BB modification. */
    xor %eax, %eax
    cpuid
    call *%r12
    cmp $43, %eax
    jne failure
#elif CASE == 2
    lea 4092(%r15), %rdi
    lea forward_payload(%rip), %rsi
    mov $10, %ecx
    rep movsb
    cmpl $0x12345678, 4092(%r15)
    jne failure
    call *%r12
    cmp $43, %eax
    jne failure
#elif CASE == 3
    lea 4099(%r15), %rdi
    lea backward_payload+9(%rip), %rsi
    mov $10, %ecx
    std
    rep movsb
    cld
    cmpl $0x12345678, 4096(%r15)
    jne failure
    call *%r12
    cmp $43, %eax
    jne failure
#elif CASE == 4
    lea 4092(%r15), %rdi
    mov $0x5a, %al
    mov $10, %ecx
    rep stosb
    cmpl $0x5a5a5a5a, 4092(%r15)
    jne failure
    movabs $0x00005a5a5a5a5a5a, %rax
    cmp %rax, 4096(%r15)
    jne failure
    call *%r12
    cmp $42, %eax
    jne failure
#elif CASE == 5
    mov $12, %ebx
    mov $43, %r14d
rewrite_loop:
    lea code_a(%rip), %rsi
    lea code_b(%rip), %rax
    test $1, %r14b
    cmovne %rax, %rsi
    mov %r12, %rdi
    mov $(code_a_end-code_a), %ecx
    rep movsb
    call *%r12
    cmp %r14d, %eax
    jne failure
    xor $1, %r14d
    dec %ebx
    jnz rewrite_loop
rewrite_loop_end:
#else
#error Unknown replay fixture
#endif
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
    mov $43, %eax
    ret
code_b_end:
code_self:
    .byte 0xb8
code_self_immediate:
    .long 42
    movb $43, code_self_immediate(%rip)
    ret
code_self_end:
forward_payload:
    .long 0x12345678
    mov $43, %eax
    ret
backward_payload:
    mov $43, %eax
    ret
    .long 0x12345678
message: .ascii "jit-replay-ok\n"
message_end:
.section .note.GNU-stack,"",@progbits

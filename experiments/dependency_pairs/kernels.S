/* Built by fixtures.py with frozen integer constants. Each slice executes four
 * operations per iteration. The only assembly difference between the serial
 * and four-chain hot loops is which application registers carry the state. */
    .global _start, hot_begin, hot_end
    .text
_start:
#if LOAD
    lea table(%rip), %rbp
    /* If f is a permutation, table[f(i)] = f(i+1) constructs one cycle.
     * f(0) = 0. The wraparound store closes the cycle explicitly. */
    xor %r12d, %r12d
    xor %ecx, %ecx
.Linit:
    inc %ecx
    mov %ecx, %eax
    imul $0x9e3779b1, %eax, %eax
    and $MASK, %eax
    mov %eax, %edx
    shr $SHIFT, %edx
    xor %edx, %eax
    imul $0x85ebca6b, %eax, %eax
    and $MASK, %eax
    mov %eax, %edx
    shr $SHIFT, %edx
    xor %edx, %eax
    mov %eax, (%rbp,%r12,4)
    mov %eax, %r12d
    cmp $MASK, %ecx
    jne .Linit
    movl $0, (%rbp,%r12,4)

    .macro step lane64, lane32
    mov (%rbp,\lane64,4), \lane32
    .endm
#else
    mov $251, %ebx
    mov $ADDEND, %edi

    .macro step lane64, lane32
    mov \lane32, %eax
    add %edi, %eax
    xor %edx, %edx
    div %ebx
    mov %edx, \lane32
    .endm
#endif

    mov $START0, %r8d
    mov $START1, %r9d
    mov $START2, %r10d
    mov $START3, %r11d
    movabs $0x21a93f5c7de10648, %r15
    mov $4, %r13d
.Lslice:
    mov $ITERATIONS, %ecx
hot_begin:
    step %r8, %r8d
#if LANES == 4
    step %r9, %r9d
    step %r10, %r10d
    step %r11, %r11d
#else
    .rept 3
    step %r8, %r8d
    .endr
#endif
    dec %ecx
    jne hot_begin
hot_end:
    /* Check the position after each quarter of the run, even when the
     * final position coincides with its seed after a whole traversal. */
    xor %r8, %r15
    rol $13, %r15
    dec %r13d
    jne .Lslice
    xor %r9, %r15
    rol $13, %r15
    xor %r10, %r15
    rol $13, %r15
    xor %r11, %r15
    mov %r15, result(%rip)

    mov $1, %eax
    mov $1, %edi
    lea result(%rip), %rsi
    mov $8, %edx
    syscall
    cmp $8, %eax
    setne %dil
    movzbl %dil, %edi
    mov $60, %eax
    syscall

    .bss
    .balign 8
result:
    .skip 8
#if LOAD
    .balign 4096
table:
    .skip (MASK + 1) * 4
#endif
    .section .note.GNU-stack,"",@progbits

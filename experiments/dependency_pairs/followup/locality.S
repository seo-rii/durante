/* Two successor fields in each eight-byte record. Both treatments build
 * exactly the same table in the same order. OFFSET selects the field. */
    .global _start, init_begin, init_end, hot_begin, hot_end
    .text
_start:
    lea table(%rip), %rbp
    xor %r12d, %r12d
    xor %ecx, %ecx
init_begin:
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
    lea 1(%r12), %esi
    and $MASK, %esi
    mov %esi, 0(%rbp,%r12,8)
    mov %eax, 4(%rbp,%r12,8)
    mov %eax, %r12d
    cmp $ENTRIES, %ecx
    jne init_begin
init_end:
    lea table+OFFSET(%rip), %rbp
    mov $START0, %r8d
    mov $START1, %r9d
    mov $START2, %r10d
    mov $START3, %r11d
    movabs $0x21a93f5c7de10648, %r15
    mov $4, %r13d
.Lslice:
    mov $ITERATIONS, %ecx
hot_begin:
    mov (%rbp,%r8,8), %r8d
#if LANES == 4
    mov (%rbp,%r9,8), %r9d
    mov (%rbp,%r10,8), %r10d
    mov (%rbp,%r11,8), %r11d
#else
    .rept 3
    mov (%rbp,%r8,8), %r8d
    .endr
#endif
    dec %ecx
    jne hot_begin
hot_end:
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
    .balign 4096
table:
    .skip ENTRIES * 8
    .section .note.GNU-stack,"",@progbits

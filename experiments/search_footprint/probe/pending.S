/* At 1022 visits only element indices 3, 20 and 208 have been sampled.
 * The other visits cover the entire 256-element table. */
.text
.global _start, terminal
_start:
    lea table(%rip), %rcx
    mov $3, %eax
    xor %edx, %edx
    mov $1022, %r8d
terminal:
    cmp %edx, (%rcx,%rax,4)
    add $17, %eax
    and $255, %eax
    dec %r8d
    jne terminal
    xor %edi, %edi
    mov $60, %eax
    syscall
.bss
.p2align 6
table: .skip 1024
.section .note.GNU-stack,"",@progbits

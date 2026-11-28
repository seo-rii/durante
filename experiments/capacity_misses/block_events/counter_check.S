/* 3 direct, 3 indirect, 6 returns and 6 DIV/IDIV in a freestanding process. */
.global _start
.text
_start:
    mov $3, %r8d
    lea signed_divide(%rip), %r9
again:
    call unsigned_divide
    call *%r9
    dec %r8d
    jnz again
    xor %edi, %edi
    mov $60, %eax
    syscall
unsigned_divide:
    mov $20, %eax
    xor %edx, %edx
    mov $2, %ecx
    div %ecx
    ret
signed_divide:
    mov $-20, %eax
    cdq
    idiv %ecx
    ret
.section .note.GNU-stack,"",@progbits

.global _start
.text
_start:
    mov $7, %ecx
    xor %eax, %eax
.Lloop:
    add $1, %eax
    dec %ecx
    jnz .Lloop
    mov $60, %eax
    xor %edi, %edi
    syscall
.section .note.GNU-stack,"",@progbits

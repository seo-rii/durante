/* Linux x86-64 static live-clock fixture. argv[1] selects read (r), readv
 * (v), EOF (e), or an EFAULT on stdin (x). Every mode executes the same
 * number of application instructions. */
.section .data
.align 8
vector: .quad byte, 1
.section .bss
byte: .skip 1
checkpoint: .skip 1

.section .text
.global _start
_start:
    mov 16(%rsp), %r8
    movzbl (%r8), %r14d
    mov $12000, %ecx
.Lwarm:
    dec %ecx
    jne .Lwarm

    xor %eax, %eax                  /* SYS_read */
    mov $19, %r10d                  /* SYS_readv */
    lea byte(%rip), %rsi
    lea vector(%rip), %r9
    cmp $'v', %r14b
    cmove %r10d, %eax
    cmove %r9, %rsi
    mov $1, %r9d                    /* Invalid address */
    cmp $'x', %r14b
    cmove %r9, %rsi
    xor %edi, %edi                  /* fd 0 */
    mov $1, %edx                    /* One byte or one iovec */
    syscall

    mov $1, %r9d
    xor %r10d, %r10d
    cmp $'e', %r14b
    cmove %r10, %r9
    mov $-14, %r10                  /* -EFAULT */
    cmp $'x', %r14b
    cmove %r10, %r9
    cmp %r9, %rax
    jmp .Lcheck
.Lcheck:
    /* ZF must survive the block charge and the write checkpoint. */
    mov %r14b, checkpoint(%rip)
    jne .Lfail
    xor %r11d, %r11d
    mov $'A', %r12d
    cmp $1, %rax
    cmove %r12d, %r11d
    cmp %r11b, byte(%rip)
    jne .Lfail
    xor %edi, %edi
    mov $60, %eax
    syscall

.Lfail:
    mov $1, %edi
    mov $60, %eax
    syscall
.section .note.GNU-stack,"",@progbits

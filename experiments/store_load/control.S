/* Linux x86-64. A single input byte ('0' or '1') selects the offset of the
 * eight-byte load after an eight-byte store. Both ranges fit in one cache
 * line, and both executions follow the same instruction and branch trace. */
.equ iterations, 96000000
.text
.globl _start
_start:
    xor %eax, %eax
    xor %edi, %edi
    lea input(%rip), %rsi
    mov $1, %edx
    syscall
    cmp $1, %eax
    jne .Lfail
    movzbl input(%rip), %r8d
    sub $'0', %r8d
    cmp $1, %r8d
    ja .Lfail
    lea line(%rip), %rdi
    mov $iterations, %ecx
    mov $1, %eax
.Lloop:
    mov %rax, (%rdi)
    mov (%rdi,%r8), %rax
    add $1, %rax
    dec %ecx
    jne .Lloop

    /* At offset 0 the recurrence is x+1. At offset 1 it is (x>>8)+1
     * because the ninth byte stays zero. The latter holds x at one. */
    imul $iterations, %r8, %rdx
    mov $(iterations+1), %ecx
    sub %rdx, %rcx
    cmp %rcx, %rax
    jne .Lfail
    mov $1, %eax
    mov $1, %edi
    lea msg(%rip), %rsi
    mov $3, %edx
    syscall
    xor %edi, %edi
.Lexit:
    mov $60, %eax
    syscall
.Lfail:
    mov $1, %edi
    jmp .Lexit

.section .rodata
msg: .ascii "ok\n"
.bss
.p2align 6
line: .skip 64
input: .skip 1
.section .note.GNU-stack,"",@progbits

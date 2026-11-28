/* Four cold lines. The second load depends on an index, the third on a base.
   A fresh LEA then overwrites RAX with an independent address. */
.data
.balign 64
table: .quad 8
.balign 64
.quad table
.balign 64
.quad 0
.balign 64
.quad 0

.text
.globl _start
_start:
    lea table(%rip), %rdi
    mov (%rdi), %rcx
    lea (%rdi,%rcx,8), %rax
    mov (%rax), %rbx
    lea 128(%rbx), %rax
    mov (%rax), %rsi
    lea 192(%rdi), %rax
    mov (%rax), %rsi
    xor %edi, %edi
    mov $60, %eax
    syscall
.section .note.GNU-stack,"",@progbits

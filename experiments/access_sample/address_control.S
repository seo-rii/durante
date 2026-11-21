/* Vary both the application stack pointer and an index. The signed
 * displacement crosses a line boundary: 63 -> 131 is far, 64 -> 132 is
 * near. The second site spans two lines only if index scaling is honored. */
.global _start, stack_site, scaled_site
.text
_start:
    and $-64, %rsp
    sub $512, %rsp
    mov %rsp, %r12
    movb $3, 63(%r12)
    movb $7, 131(%r12)
    movw $11, 60(%r12)
    movw $17, 128(%r12)
    xor %edi, %edi
    xor %edx, %edx
    xor %ecx, %ecx
    xor %r8d, %r8d
    mov $2050, %r9d
.Lloop:
    lea 64(%r12,%rdi), %rsp
stack_site:
    movzbl -1(%rsp,%rdx,4), %eax
    add %eax, %r8d
scaled_site:
    movzwl 60(%r12,%rcx,4), %eax
    add %eax, %r8d
    xor $64, %edi
    xor $1, %edx
    xor $17, %ecx
    dec %r9d
    jne .Lloop
    xor %edi, %edi
    cmp $38950, %r8d
    setne %dil
    mov $60, %eax
    syscall
.section .note.GNU-stack,"",@progbits

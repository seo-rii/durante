/* The owner samples the live page before and after a selected event. Each
 * checkpoint writes one byte to stdout and waits for a byte on stdin. */
.section .rodata
action: .quad handler, 0x04000000, restorer, 0  /* SA_RESTORER */
.section .data
byte: .byte 1
.section .text
.global _start
_start:
    mov 16(%rsp), %r12
    call checkpoint
    cmpb $'s', (%r12)
    je .Lsignal
    cmpb $'c', (%r12)
    je .Lcmps
    cmpb $'r', (%r12)
    je .Lscas
    jmp .Ldone
.Lsignal:
    mov $13, %eax              /* rt_sigaction(SIGUSR1, &action, NULL, 8) */
    mov $10, %edi
    lea action(%rip), %rsi
    xor %edx, %edx
    mov $8, %r10d
    syscall
    test %rax, %rax
    jne .Lfail
    mov $39, %eax              /* getpid */
    syscall
    mov %eax, %edi
    mov $10, %esi
    mov $62, %eax              /* kill(self, SIGUSR1) */
    syscall
    test %rax, %rax
    jne .Lfail
    jmp .Ldone
.Lcmps:
    lea byte(%rip), %rsi
    mov %rsi, %rdi
    mov $1, %ecx
    repe cmpsb
    jmp .Ldone
.Lscas:
    lea byte(%rip), %rdi
    mov $1, %ecx
    xor %eax, %eax
    repne scasb
.Ldone:
    call checkpoint
    xor %edi, %edi
    jmp .Lexit
.Lfail:
    mov $1, %edi
.Lexit:
    mov $60, %eax
    syscall
handler:
    ret
restorer:
    mov $15, %eax              /* rt_sigreturn */
    syscall
checkpoint:
    mov $1, %edi
    mov $1, %eax               /* write(1, &byte, 1) */
    lea byte(%rip), %rsi
    mov $1, %edx
    syscall
    cmp $1, %rax
    jne .Lfail
    xor %edi, %edi
    xor %eax, %eax             /* read(0, &byte, 1) */
    syscall
    cmp $1, %rax
    jne .Lfail
    ret
.section .note.GNU-stack,"",@progbits

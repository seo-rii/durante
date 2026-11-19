/* Read the same live page as the supervisor, from inside the running program.
   A getpid syscall flushes previous work and supplies an exact baseline. */
.section .rodata
live_name: .asciz "live"
.bss
.balign 4096
buffer: .skip 8192
.text
.globl _start
_start:
    mov $2, %eax                 /* open("live", O_RDONLY) */
    lea live_name(%rip), %rdi
    xor %esi, %esi
    syscall
    test %rax, %rax
    js .Lfail
    mov %eax, %r8d
    mov $9, %eax                 /* mmap(NULL, 48, PROT_READ, MAP_SHARED, fd, 0) */
    xor %edi, %edi
    mov $48, %esi
    mov $1, %edx
    mov $1, %r10d
    xor %r9d, %r9d
    syscall
    test %rax, %rax
    js .Lfail
    mov %rax, %r15

    mov $39, %eax
    syscall
    mov 16(%r15), %r12
    mov $4091, %ecx
.Lspin1:
    loop .Lspin1
    /* The two setup instructions and LOOP charge 4093. The conditional
       branches force consecutive blocks at offsets 4093, 4095 and 4096. */
    mov 16(%r15), %r8
    jrcxz .L4095
.L4095:
    jrcxz .L4096
.L4096:
    mov 16(%r15), %r9
    cmp %r8, %r12
    jne .Lfail
    sub %r12, %r9
    cmp $4096, %r9
    jne .Lfail

    mov $39, %eax
    syscall
    mov 16(%r15), %r12
    mov $4093, %ecx
.Lspin2:
    loop .Lspin2
    mov 16(%r15), %r8
    jrcxz .L4097
.L4097:
    mov 16(%r15), %r9
    cmp %r8, %r12
    jne .Lfail
    sub %r12, %r9
    cmp $4097, %r9
    jne .Lfail

    /* A large REP publishes its bytes at the following block even when
       instructions are below the threshold. A short REP waits for the syscall. */
    lea buffer(%rip), %rdi
    mov $4096, %ecx
    cld
    rep stosb
    jrcxz .Llarge
.Llarge:
    cmpq $4096, 32(%r15)
    jne .Lfail
    mov $17, %ecx
    rep stosb
    jrcxz .Lsmall
.Lsmall:
    cmpq $4096, 32(%r15)
    jne .Lfail
    mov $39, %eax
    syscall
    cmpq $4113, 32(%r15)
    jne .Lfail
    xor %edi, %edi
    jmp .Lexit
.Lfail:
    mov $1, %edi
.Lexit:
    mov $60, %eax
    syscall
.section .note.GNU-stack,"",@progbits

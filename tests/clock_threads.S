/* Both threads run the same basic blocks with different iteration counts.
 * The futex wait executes once whether the worker has already exited or not,
 * so scheduling cannot change the exact number of application instructions. */
.bss
.balign 16
worker_stack: .skip 65536
worker_stack_end:
worker_tid: .long 0

.text
.globl _start
_start:
    /* PARENT_SETTID | CHILD_CLEARTID | THREAD | SIGHAND | VM */
    mov $0x310900, %edi
    lea worker_stack_end(%rip), %rsi
    lea worker_tid(%rip), %rdx
    mov %rdx, %r10
    xor %r8d, %r8d
    mov $56, %eax
    syscall

    test %eax, %eax
    js .Lfail
    jz .Lworker
    mov %eax, %r12d
    mov $100003, %ecx
    call .Lwork
    /* FUTEX_WAIT on the original tid. Kernel teardown clears it and wakes
     * waiters. If it was already zero the syscall returns EAGAIN. */
    mov $202, %eax
    lea worker_tid(%rip), %rdi
    xor %esi, %esi
    mov %r12d, %edx
    xor %r10d, %r10d
    syscall
    cmpl $0, worker_tid(%rip)
    jne .Lfail
    xor %edi, %edi
    mov $60, %eax
    syscall

.Lworker:
    mov $70001, %ecx
    call .Lwork
    xor %edi, %edi
    mov $60, %eax
    syscall

.Lwork:
    /* Distinct live values in each thread, including across slow boundaries. */
    mov %ecx, %r10d
    not %r10d
    mov %ecx, %r11d
    movd %ecx, %xmm0
    push %rax
    stc
.Lwrite:
    mov %ecx, (%rsp)
    loop .Lwrite
    jnc .Lfail
    mov %r11d, %ecx
.Lcount:
    dec %ecx
    jz .Ldone
    jc .Lcount
.Lfail:
    mov $81, %edi
    mov $231, %eax
    syscall
.Ldone:
    jnc .Lfail
    cmpl $1, (%rsp)
    jne .Lfail
    pop %rax
    movd %xmm0, %eax
    cmp %eax, %r11d
    jne .Lfail
    not %eax
    cmp %eax, %r10d
    jne .Lfail
    ret
.section .note.GNU-stack,"",@progbits

/* Both threads run the same basic blocks with different iteration counts.
 * The futex wait executes once whether the worker has already exited or not,
 * so scheduling cannot change the exact number of application instructions. */
.bss
.balign 16
worker_stack: .skip 65536
worker_stack_end:
worker_state: .long 0

.text
.globl _start
_start:
#ifdef CLOCK_FORK
    /* Populate the accounting state and execute the shared blocks before
     * fork. The child then charges those blocks to its own segment. */
    mov $1009, %ecx
    call .Lwork
    mov $57, %eax
#else
    /* PARENT_SETTID | CHILD_CLEARTID | THREAD | SIGHAND | VM */
    mov $0x310900, %edi
    lea worker_stack_end(%rip), %rsi
    lea worker_state(%rip), %rdx
    mov %rdx, %r10
    xor %r8d, %r8d
    mov $56, %eax
#endif
    syscall

    test %eax, %eax
    js .Lfail
    jz .Lworker
    mov %eax, %r12d
    mov $100003, %ecx
    call .Lwork
#ifdef CLOCK_FORK
    /* wait4(pid, &status, 0, NULL); also require a clean child exit. */
    mov $61, %eax
    mov %r12d, %edi
    lea worker_state(%rip), %rsi
    xor %edx, %edx
    xor %r10d, %r10d
    syscall
    cmp %r12d, %eax
    jne .Lfail
#else
    /* FUTEX_WAIT on the original tid. Kernel teardown clears it and wakes
     * waiters. If it was already zero the syscall returns EAGAIN. */
    mov $202, %eax
    lea worker_state(%rip), %rdi
    xor %esi, %esi
    mov %r12d, %edx
    xor %r10d, %r10d
    syscall
#endif
    cmpl $0, worker_state(%rip)
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
#ifdef CLOCK_READ_STDIN
    /* Activate post-input inline accounting in each caller before the
     * checked loads and stores. Use each thread's own stack as the buffer. */
    mov %ecx, %r9d
    sub $8, %rsp
    xor %eax, %eax
    xor %edi, %edi
    mov %rsp, %rsi
    mov $1, %edx
    syscall
    cmp $1, %eax
    jne .Lfail
    add $8, %rsp
    mov $10000, %ecx
.Lwarm:
    dec %ecx
    jnz .Lwarm
    mov %r9d, %ecx
#endif
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

/* Fixed mixed REP demand without libc startup work. Every work() call requests
 * MOVS 37 + 7*4 = 65 bytes and STOS 13*2 + 5*8 = 66 bytes, with three entries
 * of each kind (one zero-count entry each). Validate the resulting memory.
 * MODE: 0 ordinary; 1 fork; 2 exec; 3 failed exec; 4 thread; 5 vfork. */
#ifndef MODE
#define MODE 0
#endif
.global _start
.text
_start:
#if MODE == 2
    cmpq $2, (%rsp)
    je exec_child
#endif
    call work
#if MODE == 1 || MODE == 5
#if MODE == 1
    mov $57, %eax
#else
    mov $58, %eax
#endif
    syscall
    test %rax, %rax
    js failure
    jz child
    mov %rax, %rdi
    lea status(%rip), %rsi
    xor %edx, %edx
    xor %r10d, %r10d
    mov $61, %eax
    syscall
    test %rax, %rax
    js failure
    cmpl $0, status(%rip)
    jne failure
    call work
    call work
    call work
#elif MODE == 2 || MODE == 3
#if MODE == 2
    lea self_path(%rip), %rdi
#else
    lea missing_path(%rip), %rdi
#endif
    lea child_argv(%rip), %rsi
    xor %edx, %edx
    mov $59, %eax
    syscall
#if MODE == 2
    jmp failure
exec_child:
#else
    cmp $-2, %rax
    jne failure
#endif
    call work
    call work
#elif MODE == 4
    /* CHILD_SETTID|CHILD_CLEARTID|SYSVSEM|THREAD|SIGHAND|FILES|FS|VM.
     * Start nonzero so the parent cannot miss the child's creation. The kernel
     * clears this word only after thread teardown, including its client exit. */
    movl $1, child_tid(%rip)
    mov $0x1250f00, %edi
    lea child_stack_end(%rip), %rsi
    xor %edx, %edx
    lea child_tid(%rip), %r10
    xor %r8d, %r8d
    mov $56, %eax
    syscall
    test %rax, %rax
    js failure
    jz child
thread_wait:
    pause
    cmpl $0, child_tid(%rip)
    jne thread_wait
    call work
    call work
    call work
#endif
    mov $1, %eax
    mov $1, %edi
    lea message(%rip), %rsi
    mov $(message_end-message), %edx
    syscall
    cmp $(message_end-message), %rax
    jne failure
    mov $60, %eax
    xor %edi, %edi
    syscall
child:
    call work
    call work
    mov $60, %eax
    xor %edi, %edi
    syscall
failure:
    mov $231, %eax
    mov $91, %edi
    syscall

work:
    cld
    lea source(%rip), %rsi
    lea destination(%rip), %rdi
    mov $37, %ecx
    rep movsb
    cmpb $0x5a, destination(%rip)
    jne failure
    cmpb $0x5a, destination+36(%rip)
    jne failure
    lea destination(%rip), %rdi
    mov $0x4141, %eax
    mov $13, %ecx
    rep stosw
    cmpb $0x41, destination(%rip)
    jne failure
    cmpb $0x41, destination+25(%rip)
    jne failure
    lea source(%rip), %rsi
    lea destination(%rip), %rdi
    mov $7, %ecx
    rep movsl
    cmpb $0x5a, destination+27(%rip)
    jne failure
    lea destination(%rip), %rdi
    movabs $0x4242424242424242, %rax
    mov $5, %ecx
    rep stosq
    cmpb $0x42, destination+39(%rip)
    jne failure
    xor %ecx, %ecx
    rep movsq
    rep stosb
    cmpb $0x42, destination+39(%rip)
    jne failure
    ret

.section .rodata
source: .fill 128, 1, 0x5a
self_path: .asciz "/proc/self/exe"
missing_path: .asciz "/durante-nonexistent-reference-executable"
stage: .asciz "stage"
message: .ascii "rep-breakdown-ok\n"
message_end:
.data
.align 8
child_argv: .quad self_path, stage, 0
.bss
.align 16
destination: .skip 128
status: .skip 4
child_tid: .skip 4
.align 16
child_stack: .skip 65536
child_stack_end:
.section .note.GNU-stack,"",@progbits

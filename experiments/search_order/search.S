/* A regular fd 0 contains little-endian u64 key count and query count,
 * followed by u32 targets. The read-only table contains the even keys.
 * Match the archived Rust partition_point loop and check its u64 checksum.
 */
.text
.globl _start, search_inner, terminal
_start:
    mov $8, %eax
    xor %edi, %edi
    xor %esi, %esi
    mov $2, %edx                    /* lseek(0, 0, SEEK_END) */
    syscall
    cmp $16, %rax
    jl .Lerror
    mov %rax, %r13
    mov %rax, %rsi
    mov $9, %eax
    xor %edi, %edi
    mov $1, %edx
    mov $2, %r10d
    xor %r8d, %r8d
    xor %r9d, %r9d                  /* mmap stdin read-only */
    syscall
    cmp $-4095, %rax
    jae .Lerror
    mov (%rax), %r14
    cmp $2, %r14
    jb .Lerror
    cmp $2097152, %r14
    ja .Lerror
    mov 8(%rax), %r15
    cmp $1, %r15
    jb .Lerror
    cmp $1073741824, %r15
    ja .Lerror
    lea 16(,%r15,4), %rsi
    cmp %rsi, %r13
    jne .Lerror
    lea 16(%rax), %r12
    lea keys(%rip), %rcx
    xor %eax, %eax
.Linit:
    lea (%rax,%rax), %edx
    mov %edx, (%rcx,%rax,4)
    inc %rax
    cmp %r14, %rax
    jb .Linit
    xor %r9d, %r9d
.Lquery:
    mov (%r12), %edx
    add $4, %r12
    mov %r14, %r10
    xor %eax, %eax
search_inner:
    mov %r10, %r11
    shr %r11
    lea (%rax,%r11), %rbp
    cmp %edx, (%rcx,%rbp,4)
#if USE_BRANCH
    jae .Lkeep
    mov %rbp, %rax
.Lkeep:
#else
    cmovb %rbp, %rax
#endif
    sub %r11, %r10
    cmp $1, %r10
    ja search_inner
terminal:
    cmp %edx, (%rcx,%rax,4)
    adc $0, %rax
    add %rax, %r9
    dec %r15
    jne .Lquery
    push %r9
    mov %rsp, %rsi
    mov $1, %edi
    mov $8, %edx
    mov $1, %eax
    syscall
    cmp $8, %rax
    jne .Lerror
    xor %edi, %edi
    jmp .Lexit
.Lerror:
    mov $1, %edi
.Lexit:
    mov $60, %eax
    syscall
.bss
.p2align 12
keys: .skip 8388608
.section .note.GNU-stack,"",@progbits

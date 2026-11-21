/* A regular fd 0 contains 524288 little-endian records (a, b, trip_count).
 * The only code-byte difference between the two executables is the source
 * of the TEST on the inner back edge. All divisions use the true operands.
 */
.file "gcd_loop.S"
        .text
        .global _start
        .global gcd_step
_start:
        xor %edi, %edi
        mov $6291456, %esi
        mov $1, %edx                   /* PROT_READ */
        mov $2, %r10d                  /* MAP_PRIVATE */
        xor %r8d, %r8d                 /* fd 0 */
        xor %r9d, %r9d                 /* offset */
        mov $9, %eax                   /* mmap */
        syscall
        cmp $-4095, %rax
        jae .Lerror
        mov %rax, %r12
        lea 6291456(%rax), %r13
        xor %r10d, %r10d               /* sum of measured GCDs */
        xor %r11d, %r11d               /* total decrements */
        xor %r14d, %r14d               /* counter or remainder left over */

.Lpair:
        mov (%r12), %edi
        mov 4(%r12), %edx
        mov 8(%r12), %ecx
        add %rcx, %r11
        jmp gcd_step
        .p2align 5
gcd_step:
        mov %edi, %eax
        mov %edx, %edi
        xor %edx, %edx
        div %edi
        dec %ecx
.if COUNTED
        test %ecx, %ecx
.else
        test %edx, %edx
.endif
        jne gcd_step
        or %ecx, %r14d
        or %edx, %r14d
        sub %rcx, %r11
        add %rdi, %r10
        add $12, %r12
        cmp %r13, %r12
        jne .Lpair
        test %r14d, %r14d
        jnz .Lerror
        push %r11
        push %r10
        mov %rsp, %rsi
        mov $16, %edx
        mov $1, %edi
        mov $1, %eax                   /* write */
        syscall
        cmp $16, %eax
        jne .Lerror
        xor %edi, %edi
        jmp .Lexit
.Lerror:
        mov $1, %edi
.Lexit:
        mov $60, %eax
        syscall
.section .note.GNU-stack,"",@progbits

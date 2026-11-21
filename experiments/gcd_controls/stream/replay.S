/* Replay unsigned DIV on the original operand stream. Input is a regular
 * file at fd 0 with exactly (dividend, divisor, preceding remainder) per row.
 * Feed the previous observed-minus-expected XOR, numerically zero, into
 * a different ADD destination in each variant. All rows have one division.
 */
#ifndef ADD_TO
#error Missing ADD_TO
#endif
#ifndef RECORDS
#error Missing RECORDS
#endif

.global _start, hot_begin, route_zero, hot_end
.text
_start:
    mov $8, %eax           /* lseek(0, 0, SEEK_END) */
    xor %edi, %edi
    xor %esi, %esi
    mov $2, %edx
    syscall
    cmp $(12 * RECORDS), %rax
    jne bad
    mov $9, %eax           /* mmap(0, 12 * RECORDS, PROT_READ, MAP_PRIVATE, 0, 0) */
    xor %edi, %edi
    mov $(12 * RECORDS), %esi
    mov $1, %edx
    mov $2, %r10d
    xor %r8d, %r8d
    xor %r9d, %r9d
    syscall
    cmp $-4095, %rax
    jae bad
    mov %rax, %rsi
    mov $RECORDS, %r13d
    xor %r12d, %r12d       /* sum of observed remainders */
    xor %r15d, %r15d       /* OR of any differences from the oracle */
    xor %edx, %edx         /* initial preceding remainder is zero */
.p2align 5
hot_begin:
    mov (%rsi), %eax
    mov 4(%rsi), %ecx
    xor 8(%rsi), %edx
    or %edx, %r15d
    mov $0, %ebx           /* reset the independent control's unused scratch */
route_zero:
    add %edx, ADD_TO
    xor %edx, %edx
    div %ecx
    add %rdx, %r12
    add $12, %rsi
    dec %r13
    jnz hot_begin
hot_end:
    or %edx, %r15d         /* the last GCD iteration must leave zero */
    jnz bad
    mov %r12, checksum(%rip)
    mov $1, %eax
    mov $1, %edi
    lea checksum(%rip), %rsi
    mov $8, %edx
    syscall
    cmp $8, %rax
    jne bad
    xor %edi, %edi
    jmp quit
bad:
    mov $1, %edi
quit:
    mov $60, %eax
    syscall

.bss
.p2align 3
checksum: .skip 8
.section .note.GNU-stack,"",@progbits

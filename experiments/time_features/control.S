/* Linux x86-64 branch × memory locality control. One static ELF and one hot
 * loop serve all four modes. Stdin is one digit (0..3) and LF; stdout is the
 * checksum as 16 lowercase hexadecimal digits and LF. */
.equ words, 32768
.equ mask, words - 1
.text
.global _start
_start:
    xor %eax, %eax                 /* read(0, input, 3) */
    xor %edi, %edi
    lea input(%rip), %rsi
    mov $3, %edx
    syscall
    cmp $2, %rax
    jne .Linvalid
    cmpb $10, input+1(%rip)
    jne .Linvalid
    movzbl input(%rip), %eax
    sub $'0', %eax
    cmp $3, %eax
    ja .Linvalid

    /* Bit 0 chooses word stride 1 or 257. Both are coprime to words, so
     * both layouts read exactly the same values exactly once. */
    mov %eax, %ebp
    and $1, %ebp
    shl $8, %ebp
    inc %ebp
    and $2, %eax
    mov %eax, %r8d                 /* bit 1 selects the branch pattern */
    lea values(%rip), %rdi
    xor %ecx, %ecx                 /* iteration */
    xor %ebx, %ebx                 /* word index */
    xor %r10d, %r10d               /* checksum */

.p2align 4
.Lwork:
    /* Each xor-shift and odd multiplication permutes the 15-bit domain.
     * Both patterns take the branch exactly half the time; their ordering
     * differs. Compute both candidates and select without another branch. */
    mov %ecx, %eax
    mov %eax, %edx
    shr $7, %edx
    xor %edx, %eax
    imul $25173, %eax, %eax
    and $mask, %eax
    mov %eax, %edx
    shr $5, %edx
    xor %edx, %eax
    imul $13849, %eax, %eax
    test %r8d, %r8d
    cmovz %ecx, %eax
    test $(words / 2), %eax
    jz .Lload
    add %rcx, %r10

.Lload:
    mov (%rdi,%rbx,4), %edx        /* the only load in the hot loop */
    imul %rcx, %rdx
    add %rdx, %r10
    add %ebp, %ebx
    and $mask, %ebx
    inc %ecx
    cmp $words, %ecx
    jne .Lwork

    lea output+16(%rip), %rsi
    movb $10, (%rsi)
    mov $16, %ecx
.Lhex:
    mov %r10d, %eax
    and $15, %eax
    lea '0'(%rax), %edx
    lea ('a' - 10)(%rax), %ebx
    cmp $10, %eax
    cmovae %ebx, %edx
    dec %rsi
    mov %dl, (%rsi)
    shr $4, %r10
    dec %ecx
    jne .Lhex
    mov $1, %eax                   /* write(1, output, 17) */
    mov $1, %edi
    mov $17, %edx
    syscall
    cmp $17, %rax
    jne .Linvalid
    xor %edi, %edi
    jmp .Lexit
.Linvalid:
    mov $2, %edi
.Lexit:
    mov $60, %eax
    syscall

.section .rodata
.p2align 6
values:
.set value, 1
.rept words
    .long value
    .set value, value + 1
.endr

.bss
input: .skip 3
output: .skip 17
.section .note.GNU-stack,"",@progbits

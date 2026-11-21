/* One initial fallthrough from a longer BB; one explicit jump to the
 * header with a flag-preserving NOP immediately before the Jcc. */
.macro kernel name, split
.globl \name, \name\()_header, \name\()_branch
.type \name,@function
\name:
    mov $7, %ecx
    movdqa sentinel(%rip), %xmm0
.if \split
    jmp \name\()_header
.endif
\name\()_header:
    mov $77, %eax
    xor %edx, %edx
    div %ecx
    dec %edi
.if \split
    nop
.endif
\name\()_branch:
    jnz \name\()_header
    jnz .Lbad
    cmp $7, %ecx
    jne .Lbad
    cmp $11, %eax
    jne .Lbad
    test %edx, %edx
    jnz .Lbad
    pcmpeqb sentinel(%rip), %xmm0
    pmovmskb %xmm0, %eax
    cmp $65535, %eax
    jne .Lbad
    xor %eax, %eax
    ret
.endm

.text
kernel fallthrough, 0
kernel split, 1
.Lbad:
    mov $1, %eax
    ret

/* Controlled arithmetic exception after exactly three taken back edges.
 * The signal handler exits the process normally, leaving these visits pending. */
.globl partial, partial_header, partial_branch
.type partial,@function
partial:
    mov $3, %ecx
partial_header:
    mov $9, %eax
    xor %edx, %edx
    div %ecx
    dec %ecx
partial_branch:
    jns partial_header
    jmp .Lbad

.section .rodata
.p2align 4
sentinel:
    .quad 0x123456789abcdef0, 0x3141592653589793
.section .note.GNU-stack,"",@progbits

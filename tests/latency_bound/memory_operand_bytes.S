/* A libc-free program with 623 nominal operand bytes. Keep the sentinel live
 * in XMM0 across block entries, store checkpoints and REP observations. */
.global _start
.text
_start:
    fxsave64 state(%rip)             /* 512 fixed bytes */
    mov $1, %eax
    cpuid
    bt $27, %ecx                     /* OSXSAVE */
    jnc .Lstart
    xor %eax, %eax
    xor %edx, %edx
    xsave64 state(%rip)              /* runtime-selected width contributes zero */
.Lstart:
    lea input(%rip), %rsi
    lea buffer(%rip), %rdi
    prefetchnta (%rsi)
    nopl 0(%rsi)
    movdqu (%rsi), %xmm0             /* 16 */
    movb (%rsi), %al
    movw (%rsi), %ax
    movl (%rsi), %eax
    movq (%rsi), %rax                /* reads 1+2+4+8 */
    movb %al, (%rdi)
    movw %ax, (%rdi)
    movl %eax, (%rdi)
    movq %rax, (%rdi)                /* writes 1+2+4+8 */
    addl $1, (%rdi)                  /* RMW 4+4 */
    push %rax
    pop %rax                         /* implicit stack write/read 8+8 */
    mov $3, %ecx
.Lloop:
    movb (%rsi), %al                 /* 3 visits at 1 byte each */
    dec %ecx
    jnz .Lloop
    rep movsb                        /* 1+1 even at RCX=0 */
    mov $7, %ecx
    rep movsb                        /* 1+1, independent of RCX */
    mov $2, %ecx
    rep stosw                        /* 2 */
    movdqu %xmm0, 16(%rdi)          /* 16 */
    pcmpeqb input(%rip), %xmm0       /* 16, verifies live XMM0 */
    pmovmskb %xmm0, %edi
    xor $65535, %edi
    setne %dil
    movzbl %dil, %edi
    mov $60, %eax
    syscall
.section .rodata
.balign 16
input:
    .quad 0x0123456789abcdef, 0xfedcba9876543210
.bss
.balign 64
state:
    .skip 4096
buffer:
    .skip 64
.section .note.GNU-stack,"",@progbits

/* Each of the eight integer divide encodings runs 17 times. The known
 * four-byte checksum covers every quotient. WITH_U32=0 additionally checks
 * a main module containing no eligible unsigned 32-bit divide. */
.text
.global _start
_start:
    mov $7, %ebx
    mov $73, %r15d

    .macro sample op, arg, dividend, extend, result
    mov $17, %r12d
.Lrepeat\@:
    mov $\dividend, %rax
    \extend
    \op \arg
    \result
    rol $3, %r15d
    add %eax, %r15d
    dec %r12d
    jne .Lrepeat\@
    .endm

    .macro zero
    xor %edx, %edx
    .endm
    .macro unsigned_byte
    movzbl %al,%eax
    .endm
    .macro unsigned_word
    movzwl %ax,%eax
    .endm
    .macro signed_byte
    movsbl %al,%eax
    .endm
    .macro signed_word
    movswl %ax,%eax
    .endm
    .macro keep
    .endm

    sample divb, %bl, 100, zero, unsigned_byte
    sample divw, %bx, 100, zero, unsigned_word
#if WITH_U32
    sample divl, seven(%rip), 100, zero, keep
#endif
    sample divq, %rbx, 100, zero, keep
    sample idivb, %bl, -100, cbtw, signed_byte
    sample idivw, %bx, -100, cwtd, signed_word
    sample idivl, %ebx, -100, cltd, keep
    sample idivq, %rbx, -100, cqto, keep

    mov %r15d, buffer(%rip)
    mov $1, %eax
    mov $1, %edi
    lea buffer(%rip), %rsi
    mov $4, %edx
    syscall
    cmp $4, %eax
    setne %dil
    movzbl %dil,%edi
    mov $60,%eax
    syscall
.section .rodata
seven: .long 7
.bss
.balign 4
buffer: .skip 4
.section .note.GNU-stack,"",@progbits

/* Two explicit eligible scalar load PCs are active on both inputs.
 * Each loop visits the same 256 aligned words in opposite directions. */
.section .rodata
.balign 64
table:
.set word, 3
.rept 256
.long word
.set word, word+17
.endr

.section .bss
input: .skip 2
output: .skip 17

.section .text
.global _start, forward_load, reverse_load
_start:
    xor %eax, %eax
    xor %edi, %edi
    lea input(%rip), %rsi
    mov $2, %edx
    syscall
    cmp $2, %eax
    jne invalid
    cmpb $10, input+1(%rip)
    jne invalid
    mov $7920000, %r8d
    mov $80000, %r9d
    cmpb $65, input(%rip)
    je traverse
    cmpb $66, input(%rip)
    jne invalid
    mov $400000, %r8d
    mov $7600000, %r9d
traverse:
    lea table(%rip), %rsi
    xor %edi, %edi
    xor %eax, %eax
forward_load:
    mov (%rsi,%rax,4), %edx
    add %rdx, %rdi
    inc %al
    dec %r8d
    jnz forward_load
    mov $255, %eax
reverse_load:
    mov (%rsi,%rax,4), %edx
    xor $0x5a, %edx
    add $7, %edx
    add %rdx, %rdi
    dec %al
    dec %r9d
    jnz reverse_load

    /* Print the measured sum as 16 lowercase hexadecimal digits and a newline. */
    lea output+16(%rip), %rsi
    movb $10, (%rsi)
    mov $16, %ecx
digit:
    mov %edi, %eax
    and $15, %eax
    cmp $10, %eax
    jb decimal
    add $39, %al
decimal:
    add $48, %al
    dec %rsi
    mov %al, (%rsi)
    shr $4, %rdi
    dec %ecx
    jnz digit
    mov $1, %eax
    mov $1, %edi
    mov $17, %edx
    syscall
    cmp $17, %eax
    jne invalid
    xor %edi, %edi
    jmp done
invalid:
    mov $2, %edi
done:
    mov $60, %eax
    syscall
.section .note.GNU-stack,"",@progbits

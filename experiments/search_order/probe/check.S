/* Hand oracle for the query-end sampler. Read an unsigned visit count from stdin.
 * Output the sum of the indices after the original CMP/ADC and an XMM sentinel. */
.text
.globl _start, terminal, bad_site
_start:
    mov $0, %eax
    xor %edi, %edi
    lea input(%rip), %rsi
    mov $8, %edx
    syscall
    cmp $8, %rax
    jne error
    movabs $0x3456789abcdef102, %rax
    movq %rax, %xmm0
    mov input(%rip), %r9
    lea table(%rip), %rcx
    lea indices(%rip), %rsi
    xor %rdi, %rdi
    xor %r11, %r11
    test %r9, %r9
    jz output
loop:
    mov %edi, %eax
    and $3, %eax
    mov (%rsi,%rax,8), %rax
    mov %edi, %edx
    and $1, %edx
terminal:
    cmp %edx, (%rcx,%rax,4)
    adc $0, %rax
    add %rax, %r11
bad_site:
    inc %rdi
    cmp %r9, %rdi
    jb loop
output:
    mov %r11, result(%rip)
    movq %xmm0, result+8(%rip)
    mov $1, %eax
    mov $1, %edi
    lea result(%rip), %rsi
    mov $16, %edx
    syscall
    cmp $16, %rax
    jne error
    xor %edi, %edi
    jmp exit
error:
    mov $1, %edi
exit:
    mov $60, %eax
    syscall
.section .rodata
indices:
    .quad 16, 127, 128, 0
.bss
.p2align 6
table: .skip 1024
input: .skip 8
result: .skip 16
.section .note.GNU-stack,"",@progbits

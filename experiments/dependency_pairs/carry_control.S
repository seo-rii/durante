/* 3 * 715827882 fits in signed 32 bits. 3 * 715827883 overflows,
 * setting CF for the following ADC when the loop countdown preserves it. */
.text
.global _start
_start:
    mov $ITERATIONS, %ecx
    stc
hot_begin:
    mov $715827882, %eax
    adc $0, %eax
    imul $3, %eax
#if PRESERVE_CARRY
    dec %ecx
#else
    sub $1, %ecx
#endif
    jnz hot_begin
hot_end:
    mov %al, result(%rip)
    mov $1, %eax
    mov $1, %edi
    lea result(%rip), %rsi
    mov $1, %edx
    syscall
    xor %edi, %edi
    mov $60, %eax
    syscall
.bss
result:
    .skip 1
.section .note.GNU-stack,"",@progbits

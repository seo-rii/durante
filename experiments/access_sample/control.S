/* The same buffer at offsets 0,64,128. One hot site alternates neighboring
 * lines while carrying live ZF and an application RCX index. The other
 * alternates lines two apart before overwriting flags. */
#ifndef VISITS
#define VISITS 2050
#endif
.global _start, near_site, far_site, once_site
.text
_start:
    lea words(%rip), %rsi
    xor %ecx, %ecx
    xor %edi, %edi
    xor %r8d, %r8d
    mov $VISITS, %r9d
.Lloop:
    xor %eax, %eax
near_site:
    mov (%rsi,%rcx), %eax
    jnz .Lfail
    add %eax, %r8d
far_site:
    mov (%rsi,%rdi), %eax
    cmp $0, %eax
    je .Lfail
    add %eax, %r8d
    xor $64, %ecx
    xor $128, %edi
    dec %r9d
    jne .Lloop
    cmp $((VISITS/2)*31+(VISITS%2)*14), %r8d
    jne .Lfail
once_site:
    mov (%rsi), %eax
    cmp $7, %eax
    jne .Lfail
    xor %edi, %edi
    jmp .Lexit
.Lfail:
    mov $1, %edi
.Lexit:
    mov $60, %eax
    syscall

.data
.balign 64
words:
    .long 7
    .skip 60
    .long 8
    .skip 60
    .long 9
.section .note.GNU-stack,"",@progbits

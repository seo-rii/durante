/* One explicit indexed load per visit. Most adjacent visits alternate lines
 * two apart. Visits 2, 1026 and 2050 return to line zero, so the sampler
 * observes only neighboring pairs. Build as a PIE to check relocated PCs. */
.global _start, phase_site
.text
_start:
    lea words(%rip), %rsi
    xor %ecx, %ecx
    xor %r8d, %r8d
.Lloop:
    mov %ecx, %edi
    and $1, %edi
    shl $7, %edi
    mov %ecx, %eax
    and $1023, %eax
    cmp $1, %eax
    jne .Lload
    xor %edi, %edi
.Lload:
phase_site:
    mov (%rsi,%rdi), %eax
    add %eax, %r8d
    inc %ecx
    cmp $2050, %ecx
    jne .Lloop
    cmp $16394, %r8d
    jne .Lfail
    /* Return the runtime PC through stdout to check that ELF-relative site
     * identities agree across invocations even when the load address moves. */
    lea phase_site(%rip), %rax
    push %rax
    mov %rsp, %rsi
    mov $8, %edx
    mov $1, %edi
    mov $1, %eax
    syscall
    pop %rdx
    cmp $8, %rax
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
    .skip 124
    .long 9
.section .note.GNU-stack,"",@progbits

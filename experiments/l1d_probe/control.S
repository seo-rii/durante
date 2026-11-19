/* Linux x86-64, no libc. All data accesses are relative to one page-aligned
   buffer, so the expected counters do not depend on the loader or stack. */
.bss
.balign 4096
data: .skip 9 * 4096

.text
.globl _start
_start:
    lea data(%rip), %rbx
    movabs $0x123456789abcdef0, %r10
    movabs $0xfedcba9876543210, %r11
    mov %r10, %rdx
    movq %r11, %xmm0
    cmp %r10, %rdx
    stc

    /* Keep CF, ZF, GPRs and XMM0 live across 128 scalar loads.
       All loads address the same cache line. */
    .rept 128
        mov (%rbx), %eax
    .endr
    jnc .Lfail
    jne .Lfail
    cmp %r10, %rdx
    jne .Lfail
    movq %xmm0, %r8
    cmp %r11, %r8
    jne .Lfail

    /* Fill one 8-way set. Promote line 0, then insert a ninth tag.
       LRU retains line 0 and evicts line 1, which we immediately revisit. */
    mov %rbx, %r8
    mov $8, %ecx
.Lfill:
    mov (%r8), %al
    add $4096, %r8
    dec %ecx
    jnz .Lfill
    mov (%rbx), %al
    mov 32768(%rbx), %al
    mov (%rbx), %al
    mov 4096(%rbx), %al

    /* Each 8-byte access at offset 60 touches two lines. The RMW emits
       both a read and a write. ECX is zero for the indexed 4-byte load. */
    movq $7, 60(%rbx)
    addq $5, 60(%rbx)
    jmp .Lskip
    incq 8192(%rbx)
.Lskip:
    movl 62(%rbx,%rcx), %eax

    /* The overlapping, descending copy shifts byte 128 to 129 and clears
       byte 128. The forward word stores start at the last byte of line 3. */
    movb $42, 128(%rbx)
    std
    lea 128(%rbx), %rsi
    lea 129(%rbx), %rdi
    mov $66, %ecx
    rep movsb
    cld
    mov $0xabcd, %eax
    lea 255(%rbx), %rdi
    mov $3, %ecx
    rep stosw
    cmpb $42, 129(%rbx)
    jne .Lfail
    cmpb $0, 128(%rbx)
    jne .Lfail
    cmpq $12, 60(%rbx)
    jne .Lfail
    cmpb $0xcd, 255(%rbx)
    jne .Lfail

    /* The base and index can both be instrumentation scratch candidates.
       CF and ZF survive a warm read, a cold read and a zero-count shift. */
    mov %rbx, %r10
    mov $1, %r11d
    xor %ecx, %ecx
    stc
    movzbl 128(%r10,%r11), %r8d
    jnc .Lfail
    jne .Lfail
    cmp $42, %r8d
    jne .Lfail
    stc
    movzbl 319(%r10,%r11), %r8d
    jnc .Lfail
    jne .Lfail
    shll %cl, 319(%r10,%r11)
    jnc .Lfail
    jne .Lfail
    cmp %rbx, %r10
    jne .Lfail
    cmp $1, %r11
    jne .Lfail

    /* XLAT indexes with AL, even when higher RAX bits are set. Its 32-bit
       address form additionally ignores the upper half of RBX. Both hit
       the already resident line containing byte 129. */
    movabs $0x4000000000081, %rax
    xlatb
    cmp $42, %al
    jne .Lfail
    movabs $0x100000000, %r9
    or %r9, %rbx
    movabs $0x200000081, %rax
    addr32 xlatb
    cmp $42, %al
    jne .Lfail

    xor %edi, %edi
    jmp .Lexit
.Lfail:
    mov $1, %edi
.Lexit:
    mov $60, %eax
    syscall
.section .note.GNU-stack,"",@progbits

/* Straight-line groups cross the 64-site cap. Both the memory index and
 * live arithmetic flags must survive. Also check CF through a later DEC. */
.global _start, selected_site, carry_site
.text
_start:
    lea value(%rip), %rcx
    mov $3, %r11d
.Lrepeat:
    mov $0x7fffffff, %eax
    add $1, %eax
    stc
    .rept OMITTED
        mov (%rcx), %eax
    .endr
selected_site:
    mov (%rcx), %r9d
    pushfq
    pop %r8
    and $0x8d5, %r8d
    cmp $0x895, %r8d
    jne .Lfail
    cmp $7, %r9d
    jne .Lfail
    stc
carry_site:
    mov (%rcx), %eax
    dec %r11d
    jnc .Lfail
    jnz .Lrepeat
    xor %edi, %edi
    jmp .Lexit
.Lfail:
    mov $1, %edi
.Lexit:
    mov $60, %eax
    syscall
.data
value: .long 7
.section .note.GNU-stack,"",@progbits

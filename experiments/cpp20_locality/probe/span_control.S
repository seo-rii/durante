/* Visits 1, 2, 1025 and 1026 start at byte offsets 128, 64, 0 and
 * 192. All unsampled visits start farther away at offset 512. */
.global _start, walk_site
.text
_start:
    lea words(%rip), %rsi
    xor %edi, %edi
    xor %r8d, %r8d
    mov $VISITS, %r9d
    test %r9d, %r9d
    jz .Lcheck
.Lloop:
    inc %edi
    mov $512, %edx
    cmp $1, %edi
    jne .Lsecond
    mov $128, %edx
.Lsecond:
    cmp $2, %edi
    jne .Lnext_pair
    mov $64, %edx
.Lnext_pair:
    cmp $1025, %edi
    jne .Llast
    xor %edx, %edx
.Llast:
    cmp $1026, %edi
    jne walk_site
    mov $192, %edx
walk_site:
    mov (%rsi,%rdx), %eax
    add %eax, %r8d
    dec %r9d
    jne .Lloop
.Lcheck:
    xor %edi, %edi
    cmp $VISITS, %r8d
    setne %dil
    mov $60, %eax
    syscall
.data
.balign 64
words:
    .fill 129, 4, 1
.section .note.GNU-stack,"",@progbits

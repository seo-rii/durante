/* Three more successful DIVs followed by a handled arithmetic exception can
 * leave a partially traversed loop at an already trained site. */
.text
.globl mixed, mixed_header, mixed_branch
.type mixed,@function
mixed:
mixed_header:
    mov $77, %eax
    xor %edx, %edx
    div %esi
    dec %esi
    dec %edi
mixed_branch:
    jnz mixed_header
    xor %eax, %eax
    ret
.section .note.GNU-stack,"",@progbits

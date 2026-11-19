/* Matched load-dependency control. Syscalls use Linux x86-64 numbering. */
.equ nodes, 262144
.equ mask, nodes-1
.equ groups, nodes*128/4
.text
.globl _start
_start:
    xor %eax,%eax
    xor %edi,%edi
    lea input(%rip),%rsi
    mov $1,%edx
    syscall
    cmp $1,%eax
    jne .Lfail
    movzbl input(%rip),%r12d
    sub $'0',%r12d
    cmp $1,%r12d
    ja .Lfail
    lea table(%rip),%r15
    xor %r11d,%r11d
    xor %eax,%eax
    call .Lposition
    mov %eax,%r9d
.Lmake:
    inc %r11d
    mov %r11d,%eax
    call .Lposition
    mov %eax,(%r15,%r9,4)
    mov %eax,%r9d
    cmp $nodes,%r11d
    jne .Lmake

    mov $195886,%r8d
    mov $51502,%r9d
    mov $102850,%r10d
    mov $237074,%r11d
    mov $groups,%ecx
    lea .Lserial(%rip),%rax
    lea .Lparallel(%rip),%rdx
    test %r12d,%r12d
    cmovne %rdx,%rax
    jmp *%rax
.p2align 6
.Lserial:
    mov (%r15,%r8,4),%r8d
    mov (%r15,%r8,4),%r8d
    mov (%r15,%r8,4),%r8d
    mov (%r15,%r8,4),%r8d
    dec %ecx
    jnz .Lserial
    jmp .Lcheck
.p2align 6
.Lparallel:
    mov (%r15,%r8,4),%r8d
    mov (%r15,%r9,4),%r9d
    mov (%r15,%r10,4),%r10d
    mov (%r15,%r11,4),%r11d
    dec %ecx
    jnz .Lparallel
    jmp .Lcheck
.Lcheck:
    cmp $195886,%r8d
    jne .Lfail
    cmp $51502,%r9d
    jne .Lfail
    cmp $102850,%r10d
    jne .Lfail
    cmp $237074,%r11d
    jne .Lfail
    mov $1,%eax
    mov $1,%edi
    lea msg(%rip),%rsi
    mov $3,%edx
    syscall
    xor %edi,%edi
.Lexit:
    mov $60,%eax
    syscall
.Lfail:
    mov $1,%edi
    jmp .Lexit
.Lposition:
    add $173531,%eax
    and $mask,%eax
    mov %eax,%edx
    shr $7,%edx
    xor %edx,%eax
    imul $107711,%eax
    and $mask,%eax
    mov %eax,%edx
    shr $9,%edx
    xor %edx,%eax
    imul $69353,%eax
    and $mask,%eax
    mov %eax,%edx
    shr $5,%edx
    xor %edx,%eax
    ret
.section .rodata
msg: .ascii "ok\n"
.bss
.p2align 6
table: .skip nodes*4
input: .skip 1
.section .note.GNU-stack,"",@progbits

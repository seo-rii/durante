.text
.globl _start
_start:
    lea bytes+16(%rip), %rdi
    mov $1, %r8
    xor %eax, %eax
    stc                               # CF and ZF both live across clean call
    mov %rax, (%rdi)
    mov (%rdi), %rax                  # same eight bytes
    jnc .Lfail
    jnz .Lfail
    mov %rax, -8(%rdi,%r8,8)
    mov 2(%rdi), %edx                 # covered four-byte load
    mov %rax, (%rdi)
    mov (%rdi,%r8), %rcx              # partial on right
    mov %rax, (%rdi)
    mov -1(%rdi), %rdx                # partial on left
    mov %al, (%rdi)
    mov (%rdi), %edx                  # load contains the one-byte store
    mov %rax, (%rdi)
    mov 8(%rdi), %rdx                 # disjoint on right
    mov %rax, (%rdi)
    mov -8(%rdi), %rdx                # disjoint on left
    mov %rax, (%rdi)
    nop
    mov (%rdi), %rax                  # interrupted pair
    mov %rax, (%rdi)
    jmp .Ltarget
.Ltarget:
    mov (%rdi), %rax                  # a different basic block
    movq $0, (%rdi)
    mov (%rdi), %rdx                  # immediate store is outside the probe
    mov %rax, (%edi)
    mov (%rdi), %rdx                  # 32-bit address size is also excluded
    cmp $1, %r8
    jne .Lfail
    xor %edi, %edi
    jmp .Lexit
.Lfail:
    mov $1, %edi
.Lexit:
    mov $60, %eax
    syscall
.bss
.p2align 6
bytes: .skip 64
.section .note.GNU-stack,"",@progbits

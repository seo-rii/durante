/* Linux x86-64, no libc, threads, child processes or generated code.
 * Each fixture writes one independently predictable little-endian uint64.
 * Common write/exit: 8 instructions. REP counts each REP entry once.
 */
.global _start
.text
_start:
#if FIXTURE == 1
    /* 2 setup + 2 * 2,000,000 loop + 1 store + 8 common. */
    mov $2000000, %ecx
    xor %r8d, %r8d
1:  add $3, %r8
    loop 1b
    mov %r8, payload(%rip)
#elif FIXTURE == 2
    /* 2 setup + 10 * 128 body + 2 result + 8 common. */
    mov $128, %r9d
    cld
1:  lea source(%rip), %rdi
    mov $0x5a, %al
    mov $65536, %ecx
    rep stosb
    lea source(%rip), %rsi
    lea target(%rip), %rdi
    mov $65536, %ecx
    rep movsb
    dec %r9d
    jnz 1b
    mov target+65528(%rip), %r8
    mov %r8, payload(%rip)
#elif FIXTURE != 0
#error Unknown fixed overhead fixture
#endif
    mov $1, %eax
    mov $1, %edi
    lea payload(%rip), %rsi
    mov $8, %edx
    syscall
    mov $60, %eax
    xor %edi, %edi
    syscall
.data
.align 8
payload: .quad 0x0123456789abcdef
.bss
.align 64
source: .skip 65536
target: .skip 65536
.section .note.GNU-stack,"",@progbits

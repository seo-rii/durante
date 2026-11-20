/* Linux x86-64 throughput control. Copy 2 GiB in disjoint fixed-size chunks.
 * Build with -DCOPY_BYTES=32768, 65536 or 262144. Output is eight 'Z' bytes. */
#if COPY_BYTES <= 0 || (COPY_BYTES & (COPY_BYTES - 1)) != 0 || COPY_BYTES < 8 || COPY_BYTES > (1 << 30)
#error COPY_BYTES must be a power of two from 8 bytes through 1 GiB
#endif
.global _start
.text
_start:
    cld
    movabs $0x5a5a5a5a5a5a5a5a, %rax
    mov %rax, source+COPY_BYTES-8(%rip)
    mov $(2147483648 / COPY_BYTES), %r8d
1:  lea source(%rip), %rsi
    lea destination(%rip), %rdi
    mov $COPY_BYTES, %ecx
    rep movsb
    dec %r8d
    jnz 1b

    mov $1, %eax
    mov $1, %edi
    lea destination+COPY_BYTES-8(%rip), %rsi
    mov $8, %edx
    syscall
    mov $60, %eax
    xor %edi, %edi
    syscall

.bss
.balign 64
source: .skip COPY_BYTES
destination: .skip COPY_BYTES
.section .note.GNU-stack,"",@progbits

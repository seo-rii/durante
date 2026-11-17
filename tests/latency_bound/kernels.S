/* Static latency-bound fixtures without libc startup work. Each MODE runs one
 * loop ITERATIONS times and exits 0, so its blocks and their bounds can be
 * derived by hand (see tests/latency_bound.py). The loop body after the first
 * iteration is a block whose final branch targets its own start.
 * MODE: 0 multiply chain; 1 pointer chase; 2 independent ALU; 3 divide chain. */
#ifndef MODE
#define MODE 0
#endif
#ifndef ITERATIONS
#define ITERATIONS 1000
#endif
.global _start
.text
_start:
    mov $ITERATIONS, %ecx
#if MODE == 0
    mov $3, %eax
loop:
    imul %rax, %rax
    imul %rax, %rax
    dec %ecx
    jnz loop
#elif MODE == 1
    lea chain(%rip), %rax
loop:
    mov (%rax), %rax
    dec %ecx
    jnz loop
#elif MODE == 2
loop:
    add $1, %r8
    add $1, %r9
    add $1, %r10
    add $1, %r11
    add $1, %r12
    add $1, %r13
    dec %ecx
    jnz loop
#elif MODE == 3
    mov $7, %ebx
    mov $1000, %eax
loop:
    xor %edx, %edx
    div %rbx
    add $1000, %rax
    dec %ecx
    jnz loop
#else
#error "unknown MODE"
#endif
    mov $60, %eax
    xor %edi, %edi
    syscall
.data
.balign 8
chain:
    .quad chain

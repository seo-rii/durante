LOOP = r"""
.global _start
.text
_start:
    mov $100000, %ecx
    xor %eax, %eax
1:  add $1, %rax
    dec %ecx
    jnz 1b
    mov $60, %eax
    xor %edi, %edi
    syscall
"""

REP = r"""
.global _start
.text
_start:
    lea source(%rip), %rsi
    lea target(%rip), %rdi
    mov $REP_COUNT, %ecx
    rep movsb
    mov $60, %eax
    xor %edi, %edi
    syscall
.bss
.align 16
source: .skip 65536
target: .skip 65536
"""

JIT = r"""
.global _start
.text
_start:
    mov $9, %eax
    xor %edi, %edi
    mov $4096, %esi
    mov $7, %edx
    mov $34, %r10d
    mov $-1, %r8
    xor %r9d, %r9d
    syscall
    mov %rax, %r12
    mov %rax, %rdi
    lea code(%rip), %rsi
    mov $(code_end-code), %ecx
    rep movsb
    mov $1000, %ebx
1:  call *%r12
    dec %ebx
    jnz 1b
    mov $60, %eax
    xor %edi, %edi
    syscall
.section .rodata
code:
    .rept NOP_COUNT
    nop
    .endr
    mov $42, %eax
    ret
code_end:
"""

THREADS = r"""
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
static void *run(void *arg) {
    uint64_t x = (uintptr_t)arg;
    for (unsigned i=0; i<100000; ++i) x = x*1664525u + 1013904223u;
    char source[8192] = {0}, target[8192];
    void *src = source, *dst = target; size_t n = sizeof(source);
    __asm__ volatile("rep movsb" : "+S"(src), "+D"(dst), "+c"(n) : : "memory");
    return (void *)(uintptr_t)x;
}
int main(void) {
    pthread_t threads[4];
    for (uintptr_t i=0; i<4; ++i)
        if (pthread_create(&threads[i], 0, run, (void *)i)) return 2;
    for (unsigned i=0; i<4; ++i)
        if (pthread_join(threads[i], 0)) return 3;
    puts("threads-ok");
    return 0;
}
"""

GUARDS = r"""
#include <signal.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>
static void handled(int n) { (void)n; }
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    if (!strcmp(argv[1], "fork")) {
        pid_t child = fork();
        if (child < 0) return 3;
        if (!child) _exit(0);
        int status; return waitpid(child, &status, 0) < 0;
    }
    if (!strcmp(argv[1], "exec")) {
        execl("/bin/true", "true", (char *)0); return 4;
    }
    if (!strcmp(argv[1], "signal")) {
        signal(SIGUSR1, handled); raise(SIGUSR1); return 0;
    }
    return 7;
}
"""

/* Linux x86-64 static live-clock fixture. Fourteen general registers, the
 * stack pointer, the arithmetic, direction and alignment-check flags, sixteen
 * vector registers (256-bit with AVX) and two x87 registers hold known values
 * across a loop that changes none of them and is long enough to cross a few
 * thousand publication boundaries. Each boundary runs the client's callback in
 * the middle of that state. The exit status names the first part that
 * changed: 2 flags, 3 general registers, 4 vector registers, 5 x87, 6 stack
 * pointer.
 *
 * LIVE_STATE_SIGNALS adds a handler that overwrites every register it can;
 * returning from it must restore them all, wherever the signal found the
 * thread. With the value 1 a timer interrupts the loop every 500 microseconds.
 * With 2 the handler is for SIGUSR1 and a breakpoint instruction after its
 * installation hands control to a debugger, which chooses where the signal
 * arrives. Status 7 means no signal arrived. The alignment-check flag stays
 * clear here: the kernel leaves it set on entry to a handler. */
#ifdef LIVE_STATE_SIGNALS
#define FLAGS 0xcd5 /* CF PF AF ZF SF DF OF */
#else
#define FLAGS 0x40cd5 /* CF PF AF ZF SF DF OF AC */
#endif
#define ITERATIONS 3000000

.section .rodata
.align 32
pattern:
    .set value, 0x0123456789abcdef
    .rept 64
    .quad value
    .set value, value * 6364136223846793005 + 1442695040888963407
    .endr
#ifdef LIVE_STATE_SIGNALS
action: /* handler, SA_RESTORER | SA_RESTART, restorer, mask */
    .quad handler, 0x14000000, restorer, 0
timer: /* interval and first expiry, each seconds and microseconds */
    .quad 0, 500, 0, 500
#endif
general:
    .quad 0x1111111111111111, 0x2222222222222222, 0x3333333333333333, 0x4444444444444444
    .quad 0x5555555555555555, 0x6666666666666666, 0x7777777777777777, 0x8888888888888888
    .quad 0x9999999999999999, 0xaaaaaaaaaaaaaaaa, 0xbbbbbbbbbbbbbbbb, 0xcccccccccccccccc
    .quad 0xdddddddddddddddd, 0xeeeeeeeeeeeeeeee

.section .bss
.align 32
seen: .skip 512
x87_seen: .skip 32
x87_expected: .skip 32
stack_pointer: .skip 8
avx: .skip 8
signals: .skip 8

.macro each_vector operation:vararg
    .irp index, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15
    \operation \index
    .endr
.endm
.macro load_ymm index
    vmovdqu pattern + 32 * \index(%rip), %ymm\index
.endm
.macro load_xmm index
    movdqu pattern + 32 * \index(%rip), %xmm\index
.endm
.macro store_ymm index
    vmovdqu %ymm\index, seen + 32 * \index(%rip)
.endm
.macro store_xmm index
    movdqu %xmm\index, seen + 32 * \index(%rip)
.endm

.section .text
#ifdef LIVE_STATE_SIGNALS
handler:
    incq signals(%rip)
    mov $-1, %rax
    .irp register, rbx, rcx, rdx, rsi, rdi, rbp, r8, r9, r10, r11, r12, r13, r14, r15
    mov %rax, %\register
    .endr
    .irp index, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15
    pcmpeqd %xmm\index, %xmm\index
    .endr
    fldz
    /* CF and DF set, ZF clear. */
    cmp $1, %eax
    stc
    std
    ret
restorer:
    mov $15, %eax /* rt_sigreturn */
    syscall
#endif

.global _start
_start:
#ifdef LIVE_STATE_SIGNALS
    mov $13, %eax /* rt_sigaction(SIGALRM or SIGUSR1, &action, NULL, 8) */
#if LIVE_STATE_SIGNALS == 1
    mov $14, %edi
#else
    mov $10, %edi
#endif
    lea action(%rip), %rsi
    xor %edx, %edx
    mov $8, %r10d
    syscall
    test %eax, %eax
    jnz .Lsetup_failed
#if LIVE_STATE_SIGNALS == 1
    mov $38, %eax /* setitimer(ITIMER_REAL, &timer, NULL) */
    xor %edi, %edi
    lea timer(%rip), %rsi
    xor %edx, %edx
    syscall
    test %eax, %eax
#endif
    jz .Lsetup_done
.Lsetup_failed:
    mov $8, %edi
    mov $60, %eax
    syscall
.Lsetup_done:
#if LIVE_STATE_SIGNALS == 2
    int3
#endif
#endif
    /* AVX needs the CPU feature, OSXSAVE and both state components enabled. */
    mov $1, %eax
    cpuid
    and $0x18000000, %ecx
    cmp $0x18000000, %ecx
    jne .Lsse
    xor %ecx, %ecx
    xgetbv
    and $6, %eax
    cmp $6, %eax
    jne .Lsse
    movq $1, avx(%rip)
    each_vector load_ymm
    jmp .Lloaded
.Lsse:
    each_vector load_xmm
.Lloaded:
    fninit
    fldpi
    fldl2t
    mov %rsp, stack_pointer(%rip)
    lea general(%rip), %rcx
    mov 0(%rcx), %rax
    mov 8(%rcx), %rbx
    mov 16(%rcx), %rdx
    mov 24(%rcx), %rsi
    mov 32(%rcx), %rdi
    mov 40(%rcx), %rbp
    mov 48(%rcx), %r8
    mov 56(%rcx), %r9
    mov 64(%rcx), %r10
    mov 72(%rcx), %r11
    mov 80(%rcx), %r12
    mov 88(%rcx), %r13
    mov 96(%rcx), %r14
    mov 104(%rcx), %r15
    mov $ITERATIONS, %ecx
    pushq $FLAGS
    popfq
    /* LEA, JRCXZ and JMP read and write no flag and touch no memory. */
.Lloop:
    lea -1(%rcx), %rcx
    jrcxz .Ldone
    jmp .Lloop
.Ldone:
    pushfq
    pop %rcx
    pushq $0
    popfq
    and $FLAGS, %ecx
    cmp $FLAGS, %ecx
    mov $2, %ecx
    jne .Lexit
    mov $6, %ecx
    cmp stack_pointer(%rip), %rsp
    jne .Lexit
    /* Compare through the stack: no general register is free yet. */
    push %r15
    push %r14
    push %r13
    push %r12
    push %r11
    push %r10
    push %r9
    push %r8
    push %rbp
    push %rdi
    push %rsi
    push %rdx
    push %rbx
    push %rax
    mov %rsp, %rsi
    lea general(%rip), %rdi
    mov $14, %ecx
    call compare
    mov $3, %ecx
    jne .Lexit
    add $14 * 8, %rsp
    cmpq $0, avx(%rip)
    je .Lstore_sse
    each_vector store_ymm
    mov $4, %edx
    jmp .Lcompare
.Lstore_sse:
    each_vector store_xmm
    mov $2, %edx
.Lcompare:
    /* The first EDX quadwords of each 32-byte row. */
    xor %ebx, %ebx
.Lrow:
    lea seen(%rip), %rsi
    lea pattern(%rip), %rdi
    add %rbx, %rsi
    add %rbx, %rdi
    mov %edx, %ecx
    call compare
    mov $4, %ecx
    jne .Lexit
    add $32, %ebx
    cmp $512, %ebx
    jne .Lrow
    fstpt x87_seen(%rip)
    fstpt x87_seen + 16(%rip)
    fninit
    fldpi
    fldl2t
    fstpt x87_expected(%rip)
    fstpt x87_expected + 16(%rip)
    lea x87_seen(%rip), %rsi
    lea x87_expected(%rip), %rdi
    mov $4, %ecx
    call compare
    mov $5, %ecx
    jne .Lexit
#ifdef LIVE_STATE_SIGNALS
    mov $7, %ecx
    cmpq $0, signals(%rip)
    je .Lexit
#endif
    xor %ecx, %ecx
.Lexit:
    mov %ecx, %edi
    mov $60, %eax
    syscall

/* ZF is set when the ECX quadwords at RSI and RDI are equal. A conditional
 * REP would do this in one instruction, but the client does not account one. */
compare:
    mov -8(%rsi,%rcx,8), %rax
    cmp -8(%rdi,%rcx,8), %rax
    jne 1f
    dec %ecx
    jnz compare
1:
    ret

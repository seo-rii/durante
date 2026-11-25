/* Linux x86-64 static fixture: spin, without system calls, until a SIGUSR1
 * handler has run. A debugger stops the thread at the breakpoint instruction
 * once the handler is installed, later copies `count` to `sent` and delivers
 * SIGUSR1 while the thread runs client code. The handler copies `count` to
 * `seen`. The loop can end early only by that delivery, so a runtime that
 * waits for the thread's next exit from the code cache runs it to the end.
 * With SIGNAL_WAIT_REP each iteration also executes one REP MOVSB.
 * Exit: 0 handled within BOUND iterations of the send, 10 handled later,
 * 9 never handled while spinning, 8 setup failed. */
#define LIMIT 20000000
#define BOUND 2
.section .rodata
action: .quad handler, 0x14000000, restorer, 0   /* SA_RESTORER | SA_RESTART */
.section .data
.global count, sent, seen, handled
.align 8
count: .quad 0
sent: .quad 0
seen: .quad 0
handled: .quad 0
src: .byte 1
dst: .byte 0
.section .text
handler:
    mov count(%rip), %rax
    mov %rax, seen(%rip)
    movq $1, handled(%rip)
    ret
restorer:
    mov $15, %eax /* rt_sigreturn */
    syscall
.global _start
_start:
    mov $13, %eax /* rt_sigaction(SIGUSR1, &action, NULL, 8) */
    mov $10, %edi
    lea action(%rip), %rsi
    xor %edx, %edx
    mov $8, %r10d
    syscall
    test %eax, %eax
    jz 1f
    mov $8, %edi
    mov $60, %eax
    syscall
1:  int3
.Lspin:
    incq count(%rip)
#ifdef SIGNAL_WAIT_REP
    lea src(%rip), %rsi
    lea dst(%rip), %rdi
    mov $1, %ecx
    rep movsb
#endif
    cmpq $0, handled(%rip)
    jne .Ldone
    cmpq $LIMIT, count(%rip)
    jb .Lspin
    mov $9, %edi
    mov $60, %eax
    syscall
.Ldone:
    mov seen(%rip), %rax
    sub sent(%rip), %rax
    xor %edi, %edi
    cmp $BOUND, %rax
    jbe 2f
    mov $10, %edi
2:  mov $60, %eax
    syscall

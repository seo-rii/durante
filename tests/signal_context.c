#define _GNU_SOURCE
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <ucontext.h>
#include <unistd.h>

extern unsigned long probe_register(void), probe_flags(void);
extern char register_fault[], register_resume[], flags_fault[], flags_resume[];
static volatile sig_atomic_t handled;

/* Both blocks make old state look dead on their linear path. The handler
 * inspects that state, then skips the future clobber. This is legal application
 * signal-context use and cannot borrow a counter's scratch values. */
__asm__(
    ".text\n"
    ".global probe_register, register_fault, register_resume\n"
    "probe_register: push %rbx; mov $0x12345678, %ebx; xor %eax, %eax; jmp register_fault\n"
    "register_fault: mov (%rax), %rax; mov $1, %ebx; jmp register_resume\n"
    "register_resume: mov %rbx, %rax; pop %rbx; ret\n"
    ".global probe_flags, flags_fault, flags_resume\n"
    "probe_flags: xor %eax, %eax; stc; jmp flags_fault\n"
    "flags_fault: mov (%rax), %rax; xor %edx, %edx; jmp flags_resume\n"
    "flags_resume: pushfq; pop %rax; and $1, %eax; ret\n");

static void handler(int sig, siginfo_t *info, void *raw) {
    ucontext_t *context = raw;
    greg_t *registers = context->uc_mcontext.gregs;
    if (sig != SIGSEGV || info->si_addr != NULL)
        _exit(90);
    if (registers[REG_RIP] == (greg_t)register_fault) {
        if (registers[REG_RBX] != 0x12345678)
            _exit(91);
        registers[REG_RIP] = (greg_t)register_resume;
    } else if (registers[REG_RIP] == (greg_t)flags_fault) {
        if (!(registers[REG_EFL] & 1))
            _exit(92);
        registers[REG_RIP] = (greg_t)flags_resume;
    } else {
        _exit(93);
    }
    ++handled;
}

int main(void) {
    struct sigaction action = {.sa_sigaction = handler, .sa_flags = SA_SIGINFO};
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGSEGV, &action, NULL) || probe_register() != 0x12345678 ||
        probe_flags() != 1 || handled != 2)
        return 1;
    puts("signal-context-ok");
    return 0;
}

/* Signals whose frame cannot be written to the stack.  The kernel then delivers
 * SIGSEGV instead, and when the frame for SIGSEGV itself cannot be written it
 * makes SIGSEGV fatal (force_sigsegv()).
 *
 *   overflow-handler   recursion overflows the stack; SIGSEGV handler on it
 *   overflow-altstack  the same, with the handler on an alternate stack (42)
 *   ill                ud2 with rsp unwritable and a SIGILL handler
 *   usr1               SIGUSR1 to itself with rsp unwritable and a handler
 *
 * A "-alt" suffix adds a SIGSEGV handler on an alternate stack (43), "-segv" one
 * on the unwritable stack.  Handlers that run exit; reaching the end returns 2.
 */
#define _GNU_SOURCE
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

static void exit_42(int sig) { (void)sig; _exit(42); }
static void exit_43(int sig) { (void)sig; _exit(43); }
static void exit_44(int sig) { (void)sig; _exit(44); }
static volatile int sink, depth = -1;

/* Stops only at a depth no stack reaches, so the compiler sees a base case. */
__attribute__((noinline)) static int recurse(int n) {
    volatile char pad[256];
    pad[n & 255] = (char)n;
    if (n == depth)
        return 0;
    return recurse(n + 1) + pad[3];
}

static void handle(int sig, void (*handler)(int), int altstack) {
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = handler;
    if (altstack) {
        stack_t stack = {.ss_sp = malloc(1 << 16), .ss_size = 1 << 16, .ss_flags = 0};
        if (!stack.ss_sp || sigaltstack(&stack, NULL))
            _exit(3);
        action.sa_flags = SA_ONSTACK;
    }
    if (sigaction(sig, &action, NULL))
        _exit(3);
}

int main(int argc, char **argv) {
    const char *mode = argc > 1 ? argv[1] : "";
    if (strstr(mode, "-alt"))
        handle(SIGSEGV, exit_43, 1);
    if (strstr(mode, "-segv"))
        handle(SIGSEGV, exit_42, 0);
    if (!strcmp(mode, "overflow-handler") || !strcmp(mode, "overflow-altstack")) {
        handle(SIGSEGV, exit_42, !strcmp(mode, "overflow-altstack"));
        sink = recurse(0);
    } else if (!strncmp(mode, "ill", 3)) {
        handle(SIGILL, exit_44, 0);
        __asm__ volatile("mov $16, %%rsp\n\tud2" ::: "memory");
    } else if (!strncmp(mode, "usr1", 4)) {
        handle(SIGUSR1, exit_44, 0);
        long pid = getpid(), tid = syscall(SYS_gettid);
        __asm__ volatile("mov $16, %%rsp\n\tsyscall\n\tud2"
                         :
                         : "a"((long)SYS_tgkill), "D"(pid), "S"(tid), "d"((long)SIGUSR1)
                         : "rcx", "r11", "memory");
    }
    return 2;
}

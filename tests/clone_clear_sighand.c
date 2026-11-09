#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <linux/sched.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

#if !defined(__x86_64__) || !defined(SYS_clone3)
#error This regression requires Linux x86-64 clone3 headers
#endif
#ifndef CLONE_CLEAR_SIGHAND
#define CLONE_CLEAR_SIGHAND (1ULL << 32)
#endif
_Static_assert(SYS_clone3 == 435, "update the clone3 trampoline syscall number");
_Static_assert(ATOMIC_INT_LOCK_FREE == 2, "handler requires a lock-free flag");

struct observation {
    unsigned caught_default, ignored, caught_flags, ignored_flags;
    unsigned caught_restorer_null, ignored_restorer_null, error;
    uint64_t caught_mask, ignored_mask, process_mask;
    _Atomic unsigned complete;
};
static struct observation *observed;
static uint64_t expected_mask;
static const char *missing_program;
static _Atomic unsigned parent_handler_seen;

static void parent_handler(int signal_number)
{
    (void)signal_number;
    atomic_store_explicit(&parent_handler_seen, 1, memory_order_relaxed);
}

static uint64_t mask_bits(const sigset_t *mask)
{
    uint64_t result = 0;
    for (unsigned i = 1; i <= 64; ++i)
        if (sigismember(mask, (int)i) == 1)
            result |= UINT64_C(1) << (i - 1);
    return result;
}

static int process_mask(uint64_t *mask)
{
    /* Query the full kernel-sized guest mask, including libc's reserved bits. */
    return syscall(SYS_rt_sigprocmask, SIG_SETMASK, NULL, mask, sizeof(*mask));
}

static int equal_action(const struct sigaction *a, const struct sigaction *b)
{
    return a->sa_handler == b->sa_handler && a->sa_flags == b->sa_flags &&
        a->sa_restorer == b->sa_restorer && mask_bits(&a->sa_mask) == mask_bits(&b->sa_mask);
}

__attribute__((used, noinline, noreturn)) void clone_clear_child(int failed_exec)
{
    struct sigaction caught = {0}, ignored = {0};
    if (sigaction(SIGUSR1, NULL, &caught) || sigaction(SIGUSR2, NULL, &ignored) ||
        process_mask(&observed->process_mask)) {
        observed->error = 1;
    } else {
        observed->caught_default = caught.sa_handler == SIG_DFL;
        observed->ignored = ignored.sa_handler == SIG_IGN;
        observed->caught_flags = (unsigned)caught.sa_flags;
        observed->ignored_flags = (unsigned)ignored.sa_flags;
        observed->caught_restorer_null = caught.sa_restorer == NULL;
        observed->ignored_restorer_null = ignored.sa_restorer == NULL;
        observed->caught_mask = mask_bits(&caught.sa_mask);
        observed->ignored_mask = mask_bits(&ignored.sa_mask);
        /* Linux flush_signal_handlers clears metadata even for SIG_IGN. */
        if (!observed->caught_default || !observed->ignored ||
            observed->caught_flags || observed->ignored_flags ||
            !observed->caught_restorer_null || !observed->ignored_restorer_null ||
            observed->caught_mask || observed->ignored_mask ||
            observed->process_mask != expected_mask)
            observed->error = 2;
        if (raise(SIGUSR2))
            observed->error = 3;
    }
    atomic_store_explicit(&observed->complete, 1, memory_order_release);
    if (observed->error)
        _exit(80 + (int)observed->error);
    if (failed_exec) {
        char *const argv[] = {(char *)missing_program, NULL};
        char *const envp[] = {NULL};
        execve(missing_program, argv, envp);
        _exit(errno == ENOENT ? 127 : 84);
    }
    _exit(0);
}

long raw_clone3(struct clone_args *args, size_t size, int failed_exec);
/* A new child stack has no C call frame. Do not return through libc syscall().
 * RDX survives syscall and carries the third argument into the child callback.
 * The parent changes only caller-saved registers and retains its original RSP. */
__asm__(".text\n"
        ".globl raw_clone3\n"
        ".type raw_clone3,@function\n"
        "raw_clone3:\n"
        "mov $435,%eax\n"
        "syscall\n"
        "test %rax,%rax\n"
        "jnz 1f\n"
        "mov %edx,%edi\n"
        "and $-16,%rsp\n"
        "call clone_clear_child\n"
        "ud2\n"
        "1: ret\n"
        ".size raw_clone3,.-raw_clone3\n");

int main(int argc, char **argv)
{
    if (argc != 3 || (strcmp(argv[1], "normal") && strcmp(argv[1], "failed-exec") &&
                      strcmp(argv[1], "nonvm")))
        return 2;
    missing_program = argv[2];
    const int nonvm = !strcmp(argv[1], "nonvm");
    const int failed_exec = !strcmp(argv[1], "failed-exec");
    sigset_t original_mask, added_mask;
    struct sigaction caught = {0}, ignored = {0}, caught_before = {0}, ignored_before = {0};
    struct sigaction caught_after = {0}, ignored_after = {0};
    sigemptyset(&added_mask);
    sigaddset(&added_mask, SIGWINCH);
    /* A harmless blocked bit tests inheritance; never mask the tested signals. */
    if (sigprocmask(SIG_BLOCK, &added_mask, &original_mask) || process_mask(&expected_mask))
        return 3;
    const uint64_t forbidden = (UINT64_C(1) << (SIGCHLD - 1)) |
        (UINT64_C(1) << (SIGUSR1 - 1)) | (UINT64_C(1) << (SIGUSR2 - 1));
    if (expected_mask & forbidden) {
        fputs("tested signals must be unblocked in the test environment\n", stderr);
        return 4;
    }
    caught.sa_handler = parent_handler;
    caught.sa_flags = SA_RESTART;
    sigemptyset(&caught.sa_mask);
    sigaddset(&caught.sa_mask, SIGALRM);
    ignored.sa_handler = SIG_IGN;
    ignored.sa_flags = SA_RESTART;
    sigemptyset(&ignored.sa_mask);
    sigaddset(&ignored.sa_mask, SIGTERM);
    if (sigaction(SIGUSR1, &caught, NULL) || sigaction(SIGUSR2, &ignored, NULL) ||
        sigaction(SIGUSR1, NULL, &caught_before) || sigaction(SIGUSR2, NULL, &ignored_before))
        return 5;
    observed = mmap(NULL, sizeof(*observed), PROT_READ | PROT_WRITE,
                    MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    const size_t stack_size = 65536;
    void *stack = mmap(NULL, stack_size, PROT_READ | PROT_WRITE,
                       MAP_PRIVATE | MAP_ANONYMOUS | MAP_STACK, -1, 0);
    if (observed == MAP_FAILED || stack == MAP_FAILED)
        return 6;
    atomic_init(&observed->complete, 0);
    struct clone_args args = {0};
    args.flags = CLONE_CLEAR_SIGHAND | (nonvm ? 0 : CLONE_VM | CLONE_VFORK);
    args.exit_signal = SIGCHLD;
    if (!nonvm) {
        args.stack = (uintptr_t)stack;
        args.stack_size = stack_size;
    }
    long child = raw_clone3(&args, sizeof(args), failed_exec);
    if (child < 0) {
        printf("{\"clone3_errno\":%ld,\"skipped\":%s}\n", -child,
               child == -ENOSYS || child == -EPERM ? "true" : "false");
        return child == -ENOSYS || child == -EPERM ? 77 : 7;
    }
    int status = 0;
    pid_t waited;
    do {
        waited = waitpid((pid_t)child, &status, 0);
    } while (waited < 0 && errno == EINTR);
    uint64_t parent_mask = 0;
    int parent_ok = !sigaction(SIGUSR1, NULL, &caught_after) &&
        !sigaction(SIGUSR2, NULL, &ignored_after) && !process_mask(&parent_mask) &&
        equal_action(&caught_before, &caught_after) && equal_action(&ignored_before, &ignored_after) &&
        parent_mask == expected_mask;
    if (raise(SIGUSR1) || raise(SIGUSR2) ||
        !atomic_load_explicit(&parent_handler_seen, memory_order_relaxed))
        parent_ok = 0;
    uint64_t mask_after_handler = 0;
    if (process_mask(&mask_after_handler) || mask_after_handler != expected_mask)
        parent_ok = 0;
    int child_exit = waited == child && WIFEXITED(status) ? WEXITSTATUS(status) : -1;
    unsigned complete = atomic_load_explicit(&observed->complete, memory_order_acquire);
    int ok = parent_ok && complete && !observed->error && child_exit == (failed_exec ? 127 : 0);
    printf("{\"clone3_executed\":true,\"mode\":\"%s\",\"ok\":%s,"
           "\"parent_preserved\":%s,\"child_exit\":%d,\"child_complete\":%u,"
           "\"child_error\":%u,\"caught_default\":%u,\"ignored\":%u,"
           "\"caught_flags\":%u,\"ignored_flags\":%u,"
           "\"caught_restorer_null\":%u,\"ignored_restorer_null\":%u,"
           "\"caught_mask\":%" PRIu64 ",\"ignored_mask\":%" PRIu64 ","
           "\"parent_mask\":%" PRIu64 ",\"child_mask\":%" PRIu64 "}\n",
           argv[1], ok ? "true" : "false", parent_ok ? "true" : "false", child_exit,
           complete, observed->error, observed->caught_default, observed->ignored,
           observed->caught_flags, observed->ignored_flags,
           observed->caught_restorer_null, observed->ignored_restorer_null,
           observed->caught_mask, observed->ignored_mask, parent_mask, observed->process_mask);
    munmap(stack, stack_size);
    munmap(observed, sizeof(*observed));
    if (sigprocmask(SIG_SETMASK, &original_mask, NULL))
        return 8;
    return ok ? 0 : 1;
}

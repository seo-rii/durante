#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/sched.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

#if !defined(__x86_64__) || !defined(SYS_clone3)
#error This fixture requires Linux x86-64 clone3 headers
#endif

static int ready_pipe[2], gate_pipe[2];
static void *child_stack;
static const size_t stack_size = 65536;
static int use_clone3;
static enum { CHILD_EXIT, CHILD_EXEC, CHILD_FAILED_EXEC } child_action;
static int reject_first_clone;
static const char *self_program;
static volatile sig_atomic_t native_nudge_seen;
extern char **environ;

struct worker {
    int ready[2], control[2];
    pid_t tid;
    int status;
    pthread_t thread;
};

static ssize_t read_retry(int fd, void *buffer, size_t size)
{
    ssize_t count;
    do {
        count = read(fd, buffer, size);
    } while (count < 0 && errno == EINTR);
    return count;
}

static int send_byte(int fd, char byte)
{
    ssize_t count;
    do {
        count = write(fd, &byte, 1);
    } while (count < 0 && errno == EINTR);
    return count == 1;
}

static void nudge_handler(int number, siginfo_t *info, void *context)
{
    (void)context;
    /* Pinned DynamoRIO's reset nudge: version 1, action bit 1, no flags.
     * Native execution consumes it here; an instrumented process consumes it
     * in the runtime. This is a test request, never an application workload. */
    if (number == SIGILL && info->si_code == SI_QUEUE && info->si_errno == 0x10000002)
        native_nudge_seen++;
    else
        _exit(90);
}

__attribute__((used, noinline, noreturn)) void vfork_sync_child(void)
{
    char byte;
    if (!send_byte(ready_pipe[1], 'C'))
        _exit(91);
    if (read_retry(gate_pipe[0], &byte, 1) != 1 || byte != 'G')
        _exit(92);
    if (child_action != CHILD_EXIT) {
        char *const args[] = {(char *)self_program, "--leaf", NULL};
        execve(child_action == CHILD_EXEC ? self_program : "", args, environ);
        if (child_action == CHILD_EXEC || errno != ENOENT)
            _exit(94);
    }
    _exit(0);
}

long gated_clone3(struct clone_args *args, size_t size);
long gated_clone(unsigned long flags, void *stack);
/* Both syscalls give the child a separate stack. Returning through libc's
 * syscall() frame on that new stack would be invalid. */
__asm__(".text\n"
        ".globl gated_clone3\n.type gated_clone3,@function\n"
        "gated_clone3:\nmov $435,%eax\njmp 1f\n"
        ".globl gated_clone\n.type gated_clone,@function\n"
        "gated_clone:\nmov $56,%eax\n"
        "1: syscall\ntest %rax,%rax\njnz 2f\n"
        "and $-16,%rsp\ncall vfork_sync_child\nud2\n"
        "2: ret\n");

static long spawn_call(int reject)
{
    /* CLONE_THREAD requires CLONE_SIGHAND. Its deliberate omission reaches
     * the kernel and returns EINVAL after the runtime's pre-clone handling. */
    unsigned long flags = CLONE_VM | CLONE_VFORK | (reject ? CLONE_THREAD : 0);
    if (use_clone3) {
        struct clone_args args = {0};
        args.flags = flags;
        args.exit_signal = SIGCHLD;
        args.stack = (uintptr_t)child_stack;
        args.stack_size = stack_size;
        return gated_clone3(&args, sizeof(args));
    }
    return gated_clone(flags | SIGCHLD, (char *)child_stack + stack_size);
}

static int spawn(void)
{
    if (reject_first_clone && spawn_call(1) != -EINVAL) {
        (void)send_byte(ready_pipe[1], 'E');
        return 95;
    }
    /* Reuse this same thread after failure: a stale active-vfork flag must
     * fail the runtime's debug publication assertion on this next call. */
    long child = spawn_call(0);
    if (child < 0) {
        (void)send_byte(ready_pipe[1], 'E');
        return (int)child;
    }
    int status;
    pid_t waited;
    do {
        waited = waitpid((pid_t)child, &status, 0);
    } while (waited < 0 && errno == EINTR);
    return waited == child && WIFEXITED(status) ? WEXITSTATUS(status) : 93;
}

static void *run_worker(void *argument)
{
    struct worker *worker = argument;
    pid_t tid = (pid_t)syscall(SYS_gettid);
    if (write(worker->ready[1], &tid, sizeof(tid)) != sizeof(tid)) {
        worker->status = 96;
        return NULL;
    }
    char role;
    if (read_retry(worker->control[0], &role, 1) != 1) {
        worker->status = 97;
        return NULL;
    }
    worker->status = role == 'P' ? spawn() : (role == 'S' ? 0 : 98);
    return NULL;
}

__attribute__((noinline)) static void dispatch_barrier(void)
{
    /* rt_sigprocmask is non-ignorable in the pinned runtime, so each call
     * crosses its dispatcher and drains pending nudges. NULL set/oldset leaves
     * the application mask unchanged; x86-64's kernel sigset is eight bytes.
     * A successful nudge send alone is not a reset oracle. */
    for (int i = 0; i < 4; ++i)
        if (syscall(SYS_rt_sigprocmask, SIG_BLOCK, NULL, NULL, sizeof(uint64_t)))
            _exit(99);
}

int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "--leaf"))
        return 0;
    if ((argc != 3 && argc != 4) ||
        (strcmp(argv[1], "clone") && strcmp(argv[1], "clone3")) ||
        (strcmp(argv[2], "native") && strcmp(argv[2], "engine")))
        return 2;
    if (argc == 4) {
        if (!strcmp(argv[3], "exec"))
            child_action = CHILD_EXEC;
        else if (!strcmp(argv[3], "failed-exec"))
            child_action = CHILD_FAILED_EXEC;
        else if (!strcmp(argv[3], "failed-clone"))
            reject_first_clone = 1;
        else if (strcmp(argv[3], "exit"))
            return 2;
    }
    self_program = argv[0];
    use_clone3 = !strcmp(argv[1], "clone3");
    const int expected_native = !strcmp(argv[2], "native");
    struct sigaction action = {0};
    action.sa_sigaction = nudge_handler;
    action.sa_flags = SA_SIGINFO;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGILL, &action, NULL) || pipe2(ready_pipe, O_CLOEXEC) ||
        pipe2(gate_pipe, O_CLOEXEC))
        return 3;
    child_stack = mmap(NULL, stack_size, PROT_READ | PROT_WRITE,
                       MAP_PRIVATE | MAP_ANONYMOUS | MAP_STACK, -1, 0);
    if (child_stack == MAP_FAILED)
        return 4;
    struct worker workers[2] = {0};
    for (int i = 0; i < 2; ++i) {
        if (pipe2(workers[i].ready, O_CLOEXEC) ||
            pipe2(workers[i].control, O_CLOEXEC) ||
            pthread_create(&workers[i].thread, NULL, run_worker, &workers[i]))
            return 5;
        /* Wait for application entry before creating the next worker, fixing
         * the runtime thread-record insertion order even if TIDs wrap. */
        if (read_retry(workers[i].ready[0], &workers[i].tid,
                       sizeof(workers[i].tid)) != sizeof(workers[i].tid))
            return 5;
    }
    /* The pinned x86 runtime walks its 4096 all_threads buckets in ascending
     * order and inserts at the head of each bucket. Choose roles from the
     * actual TIDs so the initialized sleeper precedes the vfork parent in
     * that walk. In a shared bucket the second worker was inserted last.
     * This attempts the sleeper before the parent even when the low 12 TID
     * bits wrap. Whether it was already safe to synchronize is decided by
     * the runtime; the application oracle still requires it to exit below. */
    int sleeper = ((unsigned)workers[0].tid & 4095) <
                          ((unsigned)workers[1].tid & 4095)
        ? 0 : 1;
    int parent = 1 - sleeper;
    if (!send_byte(workers[parent].control[1], 'P'))
        return 5;
    char byte;
    if (read_retry(ready_pipe[0], &byte, 1) != 1 || byte != 'C') {
        pthread_join(workers[parent].thread, NULL);
        (void)send_byte(workers[sleeper].control[1], 'S');
        pthread_join(workers[sleeper].thread, NULL);
        fprintf(stderr, "child did not reach gate: %d\n", workers[parent].status);
        return 6;
    }
    puts("vfork-sync:child-ready");
    fflush(stdout);
    siginfo_t info = {0};
    info.si_signo = SIGILL;
    info.si_code = SI_QUEUE;
    info.si_errno = 0x10000002;
    if (syscall(SYS_rt_tgsigqueueinfo, getpid(), syscall(SYS_gettid), SIGILL, &info))
        return 7;
    dispatch_barrier();
    if (native_nudge_seen != expected_native)
        return 8;
    if (!send_byte(workers[sleeper].control[1], 'S') ||
        pthread_join(workers[sleeper].thread, NULL) || workers[sleeper].status ||
        !send_byte(gate_pipe[1], 'G') ||
        pthread_join(workers[parent].thread, NULL) || workers[parent].status)
        return 9;
    munmap(child_stack, stack_size);
    for (int i = 0; i < 2; ++i) {
        close(ready_pipe[i]);
        close(gate_pipe[i]);
        for (int j = 0; j < 2; ++j) {
            close(workers[j].ready[i]);
            close(workers[j].control[i]);
        }
    }
    puts("vfork-sync:complete");
    return 0;
}

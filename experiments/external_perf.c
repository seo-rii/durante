/* Linux x86-64 feasibility probe, not an arbitrary-program runner or sandbox. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <linux/perf_event.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#if !defined(__linux__) || !defined(__x86_64__)
#error This experiment requires Linux x86-64.
#endif

static volatile sig_atomic_t cancelled;

static void cancel(int signal_number) { cancelled = signal_number; }

static uint64_t monotonic_ms(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
        _exit(90);
    return (uint64_t)now.tv_sec * 1000 + (uint64_t)now.tv_nsec / 1000000;
}

/* Exactly four ordinary integer instructions per iteration; no REP/SIMD. */
static void work(unsigned iterations)
{
    unsigned count = iterations;
    unsigned value = 0;
    __asm__ volatile("1: add $1, %0\n\txor $0, %0\n\tsub $1, %1\n\tjnz 1b"
                     : "+r"(value), "+r"(count) : : "cc");
    if (value != iterations || count != 0)
        _exit(91);
}

static void *thread_work(void *argument)
{
    work(*(unsigned *)argument);
    return NULL;
}

static int fixture(const char *mode, unsigned iterations)
{
    /* The controller opens its event only AFTER fork: no event descriptor can
       appear here. Check even non-CLOEXEC inherited caller descriptors. */
    for (int fd = 3; fd < 1024; fd++) {
        errno = 0;
        if (fcntl(fd, F_GETFD) != -1 || errno != EBADF)
            return 92;
    }
    if (strcmp(mode, "disable") == 0) {
        if (prctl(PR_TASK_PERF_EVENTS_DISABLE, 0, 0, 0, 0) != 0)
            return 93;
    } else if (strcmp(mode, "descriptor") == 0) {
        /* Try the actual disabling ioctl on every possible inherited fd. */
        for (int fd = 3; fd < 1024; fd++) {
            errno = 0;
            if (ioctl(fd, PERF_EVENT_IOC_DISABLE, 0) != -1 || errno != EBADF)
                return 94;
        }
        char path[96];
        snprintf(path, sizeof(path), "/proc/%ld/mem", (long)getppid());
        int fd = open(path, O_RDWR | O_CLOEXEC);
        if (fd >= 0) {
            close(fd);
            return 95;
        }
        if (errno != EACCES && errno != EPERM)
            return 96;
    } else if (strcmp(mode, "fork") == 0) {
        pid_t parent = getpid();
        pid_t child = fork();
        if (child < 0)
            return 97;
        if (child == 0) {
            if (prctl(PR_SET_PDEATHSIG, SIGKILL, 0, 0, 0) != 0 || getppid() != parent)
                _exit(107);
            work(iterations);
            _exit(0);
        }
        int status;
        if (waitpid(child, &status, 0) != child || !WIFEXITED(status) || WEXITSTATUS(status))
            return 98;
        return 0;
    } else if (strcmp(mode, "thread") == 0) {
        pthread_t thread;
        if (pthread_create(&thread, NULL, thread_work, &iterations) != 0 ||
            pthread_join(thread, NULL) != 0)
            return 99;
        return 0;
    } else if (strcmp(mode, "exec") == 0) {
        char number[16];
        snprintf(number, sizeof(number), "%u", iterations);
        execl("/proc/self/exe", "external-perf", "--fixture", "control", number, NULL);
        return 100;
    } else if (strcmp(mode, "control") != 0) {
        return 101;
    }
    work(iterations);
    return 0;
}

static int valid_mode(const char *mode)
{
    return strcmp(mode, "control") == 0 || strcmp(mode, "disable") == 0 ||
           strcmp(mode, "descriptor") == 0 || strcmp(mode, "fork") == 0 ||
           strcmp(mode, "thread") == 0 || strcmp(mode, "exec") == 0;
}

/* This process has no unrelated children. The bundled fixtures cannot change
   process groups and create at most one child or thread. */
static int finish(pid_t root, int force, int *root_status, unsigned *reaped)
{
    uint64_t deadline = monotonic_ms() + 2000;
    if (force) {
        (void)kill(-root, SIGKILL);
        (void)kill(root, SIGKILL);
    }
    for (;;) {
        int status;
        pid_t child = waitpid(-1, &status, WNOHANG);
        if (child > 0) {
            (*reaped)++;
            if (child == root)
                *root_status = status;
        } else if (child == -1 && errno == ECHILD) {
            return 1;
        } else if (child == -1 && errno != EINTR) {
            return 0;
        } else if (monotonic_ms() >= deadline) {
            return 0;
        } else {
            if (force)
                (void)kill(-root, SIGKILL);
            struct timespec pause = {.tv_nsec = 1000000};
            nanosleep(&pause, NULL);
        }
    }
}

static int report(const char *state, const char *stage, int error, pid_t child,
                  int cleanup, int status, unsigned reaped, const uint64_t *reading)
{
    printf("{\"state\":\"%s\",\"stage\":\"%s\",\"errno\":%d,"
           "\"root_pid\":%ld,\"cleanup_complete\":%s,\"reaped\":%u,"
           "\"exit_code\":%d", state, stage, error, (long)child,
           cleanup ? "true" : "false", reaped,
           status >= 0 && WIFEXITED(status) ? WEXITSTATUS(status) : -1);
    if (reading != NULL)
        printf(",\"instructions\":%" PRIu64 ",\"time_enabled_ns\":%" PRIu64
               ",\"time_running_ns\":%" PRIu64, reading[0], reading[1], reading[2]);
    puts("}");
    if (strcmp(state, "observed") == 0 || strcmp(state, "native_control") == 0)
        return 0;
    return strcmp(state, "unavailable") == 0 && cleanup ? 77 : 1;
}

int main(int argc, char **argv)
{
    if (argc == 4 && strcmp(argv[1], "--fixture") == 0 && valid_mode(argv[2]) &&
        (strcmp(argv[3], "200000") == 0 || strcmp(argv[3], "2000000") == 0))
        return fixture(argv[2], (unsigned)strtoul(argv[3], NULL, 10));
    int native = argc == 4 && strcmp(argv[1], "--native") == 0;
    if (native) {
        argc--;
        argv++;
    }
    if (argc != 3 || !valid_mode(argv[1]) ||
        (strcmp(argv[2], "200000") != 0 && strcmp(argv[2], "2000000") != 0)) {
        fputs("internal fixture probe: expected a bundled mode and fixed work size\n", stderr);
        return 2;
    }
    /* Protect controller memory and proc fd access even with ptrace_scope=0.
       These are not signal, namespace, cgroup or hostile-workload isolation. */
    if (prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0 ||
        prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0 ||
        prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0)
        return report("failed", "controller_setup", errno, 0, 1, -1, 0, NULL);
    struct sigaction action = {.sa_handler = cancel};
    sigemptyset(&action.sa_mask);
    for (unsigned i = 0; i < 3; i++) {
        int signal_number[] = {SIGINT, SIGTERM, SIGPIPE};
        if (sigaction(signal_number[i], &action, NULL) != 0)
            return report("failed", "signal_setup", errno, 0, 1, -1, 0, NULL);
    }
    int gate[2], ready[2];
    if (pipe2(gate, O_CLOEXEC) != 0)
        return report("failed", "gate", errno, 0, 1, -1, 0, NULL);
    if (pipe2(ready, O_CLOEXEC) != 0) {
        int error = errno;
        close(gate[0]); close(gate[1]);
        return report("failed", "ready_pipe", error, 0, 1, -1, 0, NULL);
    }
    pid_t parent = getpid();
    pid_t child = fork();
    if (child == -1) {
        int error = errno;
        close(gate[0]); close(gate[1]);
        close(ready[0]); close(ready[1]);
        return report("failed", "fork", error, 0, 1, -1, 0, NULL);
    }
    if (child == 0) {
        close(gate[1]);
        close(ready[0]);
        if (setpgid(0, 0) != 0 || prctl(PR_SET_PDEATHSIG, SIGKILL, 0, 0, 0) != 0 ||
            getppid() != parent || prctl(PR_SET_DUMPABLE, 1, 0, 0, 0) != 0)
            _exit(102);
        struct rlimit cpu = {2, 2}, core = {0, 0}, files = {1024, 1024};
        struct rlimit memory = {256U << 20, 256U << 20}, file_size = {0, 0};
        if (setrlimit(RLIMIT_CPU, &cpu) || setrlimit(RLIMIT_CORE, &core) ||
            setrlimit(RLIMIT_NOFILE, &files) || setrlimit(RLIMIT_AS, &memory) ||
            setrlimit(RLIMIT_FSIZE, &file_size))
            _exit(103);
        if (write(ready[1], "r", 1) != 1)
            _exit(108);
        close(ready[1]);
        char token;
        ssize_t received;
        do { received = read(gate[0], &token, 1); } while (received < 0 && errno == EINTR);
        if (received != 1 || token != 'x')
            _exit(104);
        close(gate[0]);
        /* Mark caller fds CLOEXEC too; no descriptor clearing race with threads. */
        if (syscall(SYS_close_range, 3U, ~0U, 4U) != 0)
            _exit(105);
        execl("/proc/self/exe", "external-perf", "--fixture", argv[1], argv[2], NULL);
        _exit(106);
    }
    close(gate[0]);
    close(ready[1]);
    int status = -1, error = 0;
    unsigned reaped = 0;
    struct pollfd readiness = {.fd = ready[0], .events = POLLIN};
    int available;
    do { available = poll(&readiness, 1, 3000); } while (available < 0 && errno == EINTR && !cancelled);
    char token;
    int prepared = available == 1 && !cancelled && read(ready[0], &token, 1) == 1 && token == 'r';
    close(ready[0]);
    if (!prepared) {
        close(gate[1]);
        int cleanup = finish(child, 1, &status, &reaped);
        return report("failed", "child_ready", 0, child, cleanup, status, reaped, NULL);
    }
    /* The child is blocked before exec. Opening after fork keeps the perf fd
       outside its descriptor table even before CLOEXEC takes effect. Its ready
       message also guarantees dumpable=1 for the same-UID attach permission
       check; only the controller remains nondumpable. */
    struct perf_event_attr attr;
    memset(&attr, 0, sizeof(attr));
    attr.size = sizeof(attr);
    attr.type = PERF_TYPE_HARDWARE;
    attr.config = PERF_COUNT_HW_INSTRUCTIONS;
    attr.disabled = 1;
    attr.enable_on_exec = 1;
    attr.inherit = 1;
    attr.pinned = 1;
    attr.exclude_kernel = 1;
    attr.exclude_hv = 1;
    attr.read_format = PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING;
    int counter = native ? -1 :
        (int)syscall(SYS_perf_event_open, &attr, child, -1, -1, PERF_FLAG_FD_CLOEXEC);
    error = errno;
    if (counter == -1 && !native) {
        close(gate[1]);
        int cleanup = finish(child, 1, &status, &reaped);
        int unavailable = error == EACCES || error == EPERM || error == ENODEV ||
                          error == ENOENT || error == ENOSYS || error == EOPNOTSUPP;
        return report(unavailable ? "unavailable" : "failed", "perf_event_open", error,
                      child, cleanup, status, reaped, NULL);
    }
    int sent = write(gate[1], "x", 1) == 1;
    close(gate[1]);
    uint64_t deadline = monotonic_ms() + 3000;
    int stopped = !sent;
    const char *stage = sent ? "wait" : "release";
    while (!stopped && status == -1) {
        pid_t ended = waitpid(child, &status, WNOHANG);
        if (ended == child) {
            reaped++;
            break;
        }
        status = -1;
        if (cancelled || monotonic_ms() >= deadline || (ended == -1 && errno != EINTR)) {
            stopped = 1;
            stage = cancelled ? "cancelled" : "deadline_or_wait";
            break;
        }
        struct timespec pause = {.tv_nsec = 1000000};
        nanosleep(&pause, NULL);
    }
    int cleanup = finish(child, stopped, &status, &reaped);
    if (stopped || !cleanup || status == -1 || !WIFEXITED(status) || WEXITSTATUS(status) != 0) {
        if (counter >= 0)
            close(counter);
        return report("failed", stage, 0, child, cleanup, status, reaped, NULL);
    }
    if (native)
        return report("native_control", "complete", 0, child, cleanup, status, reaped, NULL);
    /* Read only after every owned descendant has exited; inherited counts have
       returned to the event. Never scale partially scheduled observations. */
    uint64_t reading[3];
    ssize_t length;
    do { length = read(counter, reading, sizeof(reading)); } while (length < 0 && errno == EINTR);
    error = length < 0 ? errno : 0;
    close(counter);
    if (length != sizeof(reading))
        return report("failed", "counter_read", error, child, cleanup, status, reaped, NULL);
    if (reading[0] == 0 || reading[1] == 0 || reading[2] != reading[1])
        return report("failed", "counter_coverage", 0, child, cleanup, status, reaped, reading);
    return report("observed", "complete", 0, child, cleanup, status, reaped, reading);
}

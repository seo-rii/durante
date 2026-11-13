/* A small launcher keeps Python's pre-exec RSS out of the measured child.
 * Internal benchmark helper for trusted fixed commands, not a sandbox.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static int64_t monotonic_ns(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
        return -1;
    return (int64_t)now.tv_sec * 1000000000 + now.tv_nsec;
}

static bool limit(int name, rlim_t maximum) {
    struct rlimit value = {maximum, maximum};
    return setrlimit(name, &value) == 0;
}

int main(int argc, char **argv) {
    if (argc < 4 || argv[3][0] != '/')
        return 2;
    char *end = NULL;
    errno = 0;
    long timeout = strtol(argv[2], &end, 10);
    if (errno || !*argv[2] || *end || timeout < 10 || timeout > 5000)
        return 2;
    umask(077);
    int report = open(argv[1], O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (report < 0)
        return 2;
    sigset_t pending, previous;
    sigemptyset(&pending);
    sigaddset(&pending, SIGCHLD);
    if (sigprocmask(SIG_BLOCK, &pending, &previous) != 0) {
        close(report);
        return 2;
    }
    int64_t started = monotonic_ns();
    if (started < 0) {
        close(report);
        return 2;
    }
    pid_t child = fork();
    if (child == 0) {
        close(report);
        if (setsid() < 0 || sigprocmask(SIG_SETMASK, &previous, NULL) != 0 ||
            !limit(RLIMIT_CORE, 0) || !limit(RLIMIT_FSIZE, 1 << 20) ||
            !limit(RLIMIT_AS, 1UL << 30) || !limit(RLIMIT_CPU, (timeout + 999) / 1000 + 1))
            _exit(125);
        execv(argv[3], argv + 3);
        _exit(126);
    }
    if (child < 0) {
        close(report);
        return 2;
    }
    bool reaped = false, timed_out = false;
    int status = 0, error = 0;
    struct rusage usage = {0};
    int64_t deadline = started + timeout * 1000000;
    while (!reaped) {
        pid_t waited = wait4(child, &status, WNOHANG, &usage);
        if (waited == child) {
            reaped = true;
            break;
        }
        if (waited < 0) {
            if (errno == EINTR)
                continue;
            error = errno;
            break;
        }
        int64_t now = monotonic_ns();
        if (now < 0) {
            error = errno;
            kill(-child, SIGKILL);
            kill(child, SIGKILL);
            break;
        }
        int64_t remaining = deadline - now;
        if (remaining <= 0) {
            if (timed_out)
                break;
            timed_out = true;
            /* child is still owned and unreaped; its PID cannot be recycled. */
            kill(-child, SIGKILL);
            kill(child, SIGKILL);
            deadline = now + 1000000000;
            continue;
        }
        struct timespec duration = {remaining / 1000000000, remaining % 1000000000};
        if (sigtimedwait(&pending, NULL, &duration) < 0 && errno != EAGAIN && errno != EINTR) {
            error = errno;
            kill(-child, SIGKILL);
            kill(child, SIGKILL);
            break;
        }
    }
    int64_t ended = monotonic_ns();
    if (ended < started)
        error = EIO;
    int code = reaped ? (WIFEXITED(status) ? WEXITSTATUS(status) : -WTERMSIG(status)) : 125;
    int length = dprintf(report,
        "{\"schema\":1,\"pid\":%ld,\"reaped\":%s,\"timed_out\":%s,"
        "\"exit_code\":%d,\"error\":%d,\"wall_ns\":%" PRId64 ","
        "\"user_us\":%" PRId64 ",\"system_us\":%" PRId64 ",\"peak_rss_kib\":%ld}\n",
        (long)child, reaped ? "true" : "false", timed_out ? "true" : "false", code, error,
        ended - started, (int64_t)usage.ru_utime.tv_sec * 1000000 + usage.ru_utime.tv_usec,
        (int64_t)usage.ru_stime.tv_sec * 1000000 + usage.ru_stime.tv_usec, usage.ru_maxrss);
    int closed = close(report);
    return !reaped || timed_out || error || code || length <= 0 || closed != 0 ? 1 : 0;
}

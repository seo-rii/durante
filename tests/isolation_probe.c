#define _GNU_SOURCE
#include <errno.h>
#include <dirent.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "probe check line %d errno %d\n", __LINE__, errno); return 2; } } while (0)
static int different_namespace(const char *kind, const char *host) {
    char path[128], value[128];
    snprintf(path, sizeof(path), "/proc/self/ns/%s", kind);
    ssize_t n = readlink(path, value, sizeof(value)-1);
    if (n <= 0) return 0;
    value[n] = 0;
    return strcmp(value, host) != 0;
}
int main(int argc, char **argv) {
    CHECK(argc == 5);
    CHECK(different_namespace("pid", argv[3]));
    CHECK(different_namespace("net", argv[4]));
    if (!strcmp(argv[1], "exit-139")) {
        puts("exiting-139");
        return 139;
    }
    if (!strcmp(argv[1], "signal-term")) {
        puts("terminating-self");
        CHECK(fflush(stdout) == 0);
        CHECK(raise(SIGTERM) == 0);
        return 2;
    }
    if (!strcmp(argv[1], "capture-exact")) {
        char out[4096], err[4096];
        memset(out, 'O', sizeof(out)); memset(err, 'E', sizeof(err));
        out[0] = 0; err[0] = (char)0xff;
        CHECK(write(STDOUT_FILENO, out, sizeof(out)) == (ssize_t)sizeof(out));
        CHECK(write(STDERR_FILENO, err, sizeof(err)) == (ssize_t)sizeof(err));
        return 0;
    }
    if (!strcmp(argv[1], "capture-flood")) {
        pid_t child = fork();
        CHECK(child >= 0);
        char data[4096]; memset(data, child ? 'E' : 'O', sizeof(data));
        for (int i = 0; i < 64; i++)
            CHECK(write(child ? STDERR_FILENO : STDOUT_FILENO, data, sizeof(data)) == (ssize_t)sizeof(data));
        if (child) CHECK(waitpid(child, NULL, 0) == child);
        return 0;
    }
    if (!strcmp(argv[1], "capture-hold") || !strcmp(argv[1], "capture-close")) {
        if (!strcmp(argv[1], "capture-close")) {
            CHECK(close(STDOUT_FILENO) == 0); CHECK(close(STDERR_FILENO) == 0);
        } else CHECK(write(STDOUT_FILENO, "held\n", 5) == 5);
        pid_t child = fork();
        CHECK(child >= 0);
        if (!child) {
            CHECK(setsid() >= 0);
            const struct timespec delay = {2, 0};
            nanosleep(&delay, NULL);
        }
        return 0;
    }
    if (!strcmp(argv[1], "sleep")) { for (;;) pause(); }
    if (!strcmp(argv[1], "kill-inner")) {
        CHECK(getppid() == 2);
        puts("killing-inner");
        CHECK(fflush(stdout) == 0);
        pid_t child = fork();
        CHECK(child >= 0);
        if (!child) {
            CHECK(setsid() >= 0);
            /* Finite even if containment regresses. No outside PID or path is
             * touched; the independent harness reaps its adopted children. */
            const struct timespec delay = {2, 0};
            nanosleep(&delay, NULL);
            _exit(0);
        }
        CHECK(kill(getppid(), SIGKILL) == 0);
        for (;;) pause();
    }
    CHECK(!strcmp(argv[1], "probe"));
    CHECK(getenv("DURANTE_HOST_SECRET") == NULL);
    CHECK(prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) == 1);
    CHECK(open(argv[2], O_RDONLY | O_CLOEXEC) == -1);
    CHECK(open("/proc/1/mem", O_RDWR | O_CLOEXEC) == -1);
    CHECK(open("/proc/1/mem", O_RDONLY | O_CLOEXEC) == -1);
    DIR *parent_fds = opendir("/proc/1/fd");
    CHECK(parent_fds == NULL);
    CHECK(open("/proc/2/mem", O_RDWR | O_CLOEXEC) == -1);
    CHECK(open("/usr/durante-forbidden", O_WRONLY | O_CREAT, 0600) == -1);
    CHECK(open("/capture/extra", O_WRONLY | O_CREAT, 0600) == -1);
    CHECK(open("/work/Main", O_WRONLY) == -1);
    CHECK(socket(AF_INET, SOCK_STREAM, 0) == -1 && errno == EPERM);
    CHECK(socket(AF_UNIX, SOCK_STREAM, 0) == -1 && errno == EPERM);
    int pair[2];
    CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, pair) == 0);
    CHECK(close(pair[0]) == 0 && close(pair[1]) == 0);
    CHECK(socketpair(AF_UNIX, SOCK_DGRAM, 0, pair) == -1 && errno == EPERM);
    CHECK(connect(-1, NULL, 0) == -1 && errno == EPERM);
    FILE *status = fopen("/proc/self/status", "r");
    CHECK(status != NULL);
    char line[256];
    int caps = 0;
    while (fgets(line, sizeof(line), status)) {
        if (!strncmp(line, "CapEff:", 7) || !strncmp(line, "CapPrm:", 7) ||
            !strncmp(line, "CapInh:", 7) || !strncmp(line, "CapAmb:", 7)) {
            unsigned long long value = 1;
            CHECK(sscanf(line+7, "%llx", &value) == 1 && value == 0);
            caps++;
        }
    }
    CHECK(fclose(status) == 0 && caps == 4);
    puts("contained");
    return 0;
}

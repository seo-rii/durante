#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <unistd.h>

/* Fixed, finite fault fixtures, never a configurable workload generator. The
 * Docker owner imposes an independent deadline and a 32-task cgroup ceiling. */
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    if (!strcmp(argv[1], "success")) {
        puts("owner-success");
        return 0;
    }
    alarm(30);
    if (!strcmp(argv[1], "oom") || !strcmp(argv[1], "publish")) {
        /* The worker can observe this stop even when stdout is buffered by
         * Durante's capture. No pressure or publication starts before the
         * outside owner requests continuation of the actual native fixture. */
        if (prctl(PR_SET_NAME, "owner-ready", 0, 0, 0) < 0 || raise(SIGSTOP)) return 14;
        if (!strcmp(argv[1], "publish")) {
            puts("owner-publication");
            return fflush(stdout) ? 4 : 0;
        }
        /* 24 finite mappings, 16 MiB each, exceed the job's 256 MiB ceiling.
         * Touch every page so virtual reservation or ENOMEM cannot count as
         * OOM coverage. The external owner also requires kernel oom_kill. */
        for (unsigned chunk = 0; chunk < 24; ++chunk) {
            volatile unsigned char *memory = mmap(NULL, 16U << 20, PROT_READ | PROT_WRITE,
                                                  MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
            if (memory == MAP_FAILED) return 15;
            for (unsigned offset = 0; offset < (16U << 20); offset += 4096)
                memory[offset] = (unsigned char)(chunk + 1);
        }
        return 16; /* Allocating the bounded ceiling is not a successful fault. */
    }
    if (!strcmp(argv[1], "pids")) {
        unsigned children = 0;
        for (; children < 48; ++children) {
            pid_t child = fork();
            if (child < 0) {
                if (errno != EAGAIN || children == 0) return 3;
                printf("owner-pids-exhausted:%u\n", children);
                if (fflush(stdout)) return 4;
                /* Retain the pressure until the separate owner tears down the
                 * job. Neither a successful exit nor accepted counts suffice. */
                for (;;) pause();
            }
            if (!child) {
                alarm(30);
                if (setsid() < 0) _exit(5);
                for (;;) pause();
            }
        }
        return 6; /* The configured task limit did not actually bind. */
    }
    if (strcmp(argv[1], "hold")) return 2;
    int ready[2];
    if (pipe2(ready, O_CLOEXEC) < 0) return 9;
    pid_t child = fork();
    if (child < 0) return 7;
    if (!child) {
        alarm(30);
        if (close(ready[0]) < 0) _exit(10);
        if (setsid() < 0) _exit(8);
        if (write(ready[1], "R", 1) != 1 || close(ready[1]) < 0) _exit(11);
        for (;;) pause();
    }
    if (close(ready[1]) < 0) return 12;
    /* The inherited parent alarm bounds this read even if the child stalls.
     * Do not claim a detached descendant until its setsid has succeeded. */
    char byte = 0;
    if (read(ready[0], &byte, 1) != 1 || byte != 'R' || close(ready[0]) < 0) return 13;
    puts("owner-running");
    if (fflush(stdout)) return 4;
    for (;;) pause();
}

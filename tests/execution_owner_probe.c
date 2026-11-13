#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
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

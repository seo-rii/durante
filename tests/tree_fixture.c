#define _GNU_SOURCE
#include <errno.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>
extern char **environ;
#ifndef PRE_WORK
#define PRE_WORK 10000
#endif
#ifndef CHILD_WORK
#define CHILD_WORK 50000
#endif
static void work(unsigned n) {
  __asm__ volatile("1: add $1, %%rax; dec %%ecx; jnz 1b"
                   : "+c"(n)
                   :
                   : "rax", "cc");
}
static int wait_child(pid_t p) {
  int status;
  return p < 0 || waitpid(p, &status, 0) != p || !WIFEXITED(status) ||
         WEXITSTATUS(status);
}
static void handled(int sig) { (void)sig; }
int main(int argc, char **argv) {
  if (argc < 2)
    return 2;
  const char *mode = argv[1];
  if (!strcmp(mode, "leaf")) {
    work(CHILD_WORK);
    puts("leaf");
    return 0;
  }
  if (!strcmp(mode, "signal")) {
    signal(SIGUSR1, handled);
    raise(SIGUSR1);
    work(1000);
    return 0;
  }
  if (!strcmp(mode, "nonzero"))
    return 7;
  if (!strcmp(mode, "failed-exec")) {
    execl("/durante-nonexistent-executable", "bad", (char *)0);
    if (errno != ENOENT)
      return 8;
    work(1000);
    return 0;
  }
  if (!strcmp(mode, "exec-empty-env")) {
    char *args[] = {argv[0], "leaf", NULL};
    char *env[] = {NULL};
    execve(argv[0], args, env);
    return 9;
  }
  if (!strcmp(mode, "vfork-exit") || !strcmp(mode, "vfork-failed-exec")) {
    pid_t p = vfork();
    if (!p) {
      if (!strcmp(mode, "vfork-failed-exec"))
        execl("/durante-nonexistent-executable", "bad", (char *)0);
      work(CHILD_WORK);
      _exit(0);
    }
    return wait_child(p);
  }
  if (!strcmp(mode, "exec")) {
    execl(argv[0], argv[0], "leaf", (char *)0);
    return 9;
  }
  if (!strcmp(mode, "exec-chain")) {
    execl(argv[0], argv[0], "exec", (char *)0);
    return 9;
  }
  if (!strcmp(mode, "posix-spawn")) {
    pid_t p;
    char *args[] = {argv[0], "leaf", NULL};
    if (posix_spawn(&p, argv[0], NULL, NULL, args, environ))
      return 10;
    return wait_child(p);
  }
  if (!strcmp(mode, "vfork")) {
    pid_t p = vfork();
    if (!p) {
      execl(argv[0], argv[0], "leaf", (char *)0);
      _exit(11);
    }
    return wait_child(p);
  }
  work(PRE_WORK);
  pid_t p = fork();
  if (p < 0)
    return 3;
  if (!p) {
    if (!strcmp(mode, "fork-exec")) {
      execl(argv[0], argv[0], "leaf", (char *)0);
      _exit(4);
    }
    if (!strcmp(mode, "kill-child")) {
      for (;;)
        pause();
    }
    if (!strcmp(mode, "nested")) {
      pid_t q = fork();
      if (!q) {
        work(CHILD_WORK);
        _exit(0);
      }
      if (wait_child(q))
        _exit(5);
    }
    work(CHILD_WORK);
    _exit(0);
  }
  if (!strcmp(mode, "kill-child")) {
    usleep(100000);
    kill(p, SIGKILL);
    int status;
    if (waitpid(p, &status, 0) != p || !WIFSIGNALED(status))
      return 12;
    return 0;
  }
  return wait_child(p);
}

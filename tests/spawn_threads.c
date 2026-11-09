#define _GNU_SOURCE
#include <pthread.h>
#include <spawn.h>
#include <stdio.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>
extern char **environ;
static char *program;
static void *worker(void *unused) {
  (void)unused;
  for (int i = 0; i < 2; ++i) {
    pid_t p;
    char *args[] = {program, "leaf", NULL};
    int status;
    if (posix_spawn(&p, program, NULL, NULL, args, environ) ||
        waitpid(p, &status, 0) != p || !WIFEXITED(status) ||
        WEXITSTATUS(status))
      return (void *)1;
  }
  return NULL;
}
int main(int argc, char **argv) {
  if (argc == 2 && !strcmp(argv[1], "leaf")) {
    unsigned n = 10000;
    __asm__ volatile("1: add $1, %%rax; dec %%ecx; jnz 1b"
                     : "+c"(n)
                     :
                     : "rax", "cc");
    return 0;
  }
  program = argv[0];
  pthread_t threads[3];
  for (int i = 0; i < 3; ++i)
    if (pthread_create(&threads[i], NULL, worker, NULL))
      return 2;
  for (int i = 0; i < 3; ++i) {
    void *result;
    if (pthread_join(threads[i], &result) || result)
      return 3;
  }
  puts("spawn-threads-ok");
  return 0;
}

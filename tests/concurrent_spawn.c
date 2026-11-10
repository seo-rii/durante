#define _GNU_SOURCE
#include <errno.h>
#include <pthread.h>
#include <spawn.h>
#include <stdbool.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

static pthread_barrier_t barrier;
static const char *leaf;
static unsigned rounds;
static bool failed_exec, orphan, empty_env;
static _Atomic unsigned saw_sigchld;
_Static_assert(ATOMIC_INT_LOCK_FREE == 2, "SIGCHLD flag must be lock-free");

static void child_signal(int signal_number) {
  (void)signal_number;
  atomic_store_explicit(&saw_sigchld, 1, memory_order_relaxed);
}

static void *worker(void *unused) {
  (void)unused;
  bool error = false;
  for (unsigned iteration = 0; iteration < rounds; ++iteration) {
    int ready = pthread_barrier_wait(&barrier);
    if (ready != 0 && ready != PTHREAD_BARRIER_SERIAL_THREAD)
      error = true;
    pid_t child = -1;
    char *args[] = {(char *)leaf, NULL};
    char *empty[] = {NULL};
    int status = posix_spawn(&child,
                            failed_exec ? "/durante-no-such-program" : leaf,
                            NULL, NULL, args, empty_env ? empty : environ);
    if (failed_exec) {
      /* glibc waits for its failed-exec child before returning ENOENT. */
      if (status != ENOENT)
        error = true;
    } else if (status != 0) {
      error = true;
    } else if (!orphan) {
      int wait_status;
      pid_t waited;
      do {
        waited = waitpid(child, &wait_status, 0);
      } while (waited < 0 && errno == EINTR);
      if (waited != child || !WIFEXITED(wait_status) || WEXITSTATUS(wait_status))
        error = true;
    }
    /* Keep visiting every barrier on failure so a worker cannot deadlock peers. */
  }
  return error ? (void *)1 : NULL;
}

static unsigned number(const char *text, unsigned maximum) {
  char *end;
  unsigned long value = strtoul(text, &end, 10);
  return *text && !*end && value > 0 && value <= maximum ? (unsigned)value : 0;
}

int main(int argc, char **argv) {
  if (argc != 5)
    return 2;
  const char *mode = argv[1];
  leaf = argv[2];
  unsigned threads = number(argv[3], 16);
  rounds = number(argv[4], 64);
  if (!threads || !rounds || threads * rounds > 240)
    return 2;
  bool custom_handler = !strcmp(mode, "failed-exec-handler");
  failed_exec = !strcmp(mode, "failed-exec") || custom_handler;
  orphan = !strcmp(mode, "orphan");
  empty_env = !strcmp(mode, "empty-env");
  if (strcmp(mode, "success") && !failed_exec && !orphan && !empty_env)
    return 2;
  if (custom_handler) {
    struct sigaction action = {0};
    action.sa_handler = child_signal;
    action.sa_flags = SA_RESTART;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGCHLD, &action, NULL))
      return 6;
  }
  if (pthread_barrier_init(&barrier, NULL, threads))
    return 3;
  pthread_t ids[16];
  for (unsigned i = 0; i < threads; ++i)
    if (pthread_create(&ids[i], NULL, worker, NULL))
      return 4;
  bool error = false;
  for (unsigned i = 0; i < threads; ++i) {
    void *result;
    if (pthread_join(ids[i], &result) || result)
      error = true;
  }
  pthread_barrier_destroy(&barrier);
  if (error)
    return 5;
  /* Standard signals can coalesce; require observation, not one signal/child. */
  if (custom_handler && !atomic_load_explicit(&saw_sigchld, memory_order_relaxed))
    return 7;
  /* In orphan mode every spawn call and worker has finished, but the delayed
   * children have not been waited. This is not exit_group during active spawn. */
  puts("concurrent-spawn-ok");
  return 0;
}

/* Generated code that keeps changing while other threads run it.
 *
 * A patcher thread keeps changing a set of generated functions while worker
 * threads call them. Each function has its own version counter, odd while that
 * function is being changed, and returns its argument plus (version / 2) % 100.
 * A call between two reads of the same even version must return that version's
 * result. Natively this always holds; under a code cache it fails when the
 * cache keeps running a stale copy.
 *
 * Modes choose how the code changes and what else the process does meanwhile:
 *   inplace  functions on RWX pages are patched in place; one of them has an
 *            instruction that crosses a page boundary
 *   wx       the pages are RX, and made RWX only around each patch
 *   remap    the page is replaced by a new mapping with the new code
 *   churn    inplace, with workers that are short-lived threads
 *   signal   inplace, with signals whose handler also calls the functions
 *   fork     inplace, with children forked while the code changes
 *
 * Usage: jit_stress MODE [ROUNDS [THREADS]]. Prints one JSON line.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

#define PAGE 4096
#define PAGES 3
#define FUNCTIONS 6
#define BODY 8 /* mov eax, imm32; add eax, edi; ret */

typedef int (*fn_t)(int);

enum { INPLACE, WX, REMAP, CHURN, SIGNAL, FORK };
static const char *const mode_names[] = {"inplace", "wx", "remap", "churn", "signal", "fork"};

static int mode;
static unsigned char *code;
static size_t offsets[FUNCTIONS];
static atomic_int versions[FUNCTIONS];
static atomic_int stop;
static atomic_long calls, stale, signals, threads_created, forks, bad_children;
/* Callers of run_calls() that have made a call: the changes start only then. */
static atomic_int ready;
static int memfd = -1;

static int expected(int version) { return (version / 2) % 100; }

static void write_body(unsigned char *page_base, size_t offset, int value) {
  unsigned char body[BODY] = {0xb8, 0, 0, 0, 0, 0x01, 0xf8, 0xc3};
  memcpy(body + 1, &value, 4);
  memcpy(page_base + offset, body, BODY);
}

/* One checked call. Returns 1 if it ran, 0 if the function was being changed. */
static int checked_call(int index, int argument, long *bad) {
  int before = atomic_load(&versions[index]);
  if (before % 2 != 0)
    return 0;
  int result = ((fn_t)(code + offsets[index]))(argument);
  if (atomic_load(&versions[index]) == before && result != argument + expected(before))
    ++*bad;
  return 1;
}

static void run_calls(long limit) {
  long n = 0, bad = 0;
  int counted = 0;
  for (int index = 0; limit < 0 ? !atomic_load(&stop) : n < limit;
       index = (index + 1) % FUNCTIONS) {
    n += checked_call(index, (int)(n & 0xff), &bad);
    if (!counted && n > 0) {
      atomic_fetch_add(&ready, 1);
      counted = 1;
    }
  }
  atomic_fetch_add(&calls, n);
  atomic_fetch_add(&stale, bad);
}

static void *worker(void *arg) {
  (void)arg;
  run_calls(-1);
  return NULL;
}

static void *short_worker(void *arg) {
  (void)arg;
  run_calls(2000);
  return NULL;
}

/* Keeps one short-lived worker running at a time. */
static void *spawner(void *arg) {
  (void)arg;
  while (!atomic_load(&stop)) {
    pthread_t thread;
    if (pthread_create(&thread, NULL, short_worker, NULL) != 0)
      break;
    pthread_join(thread, NULL);
    atomic_fetch_add(&threads_created, 1);
  }
  return NULL;
}

static void on_signal(int number) {
  (void)number;
  long bad = 0;
  int saved = errno;
  for (int index = 0; index < FUNCTIONS; ++index)
    checked_call(index, 7, &bad);
  atomic_fetch_add(&stale, bad);
  atomic_fetch_add(&signals, 1);
  errno = saved;
}

struct signaler {
  pthread_t *targets;
  int count;
};

static void *signaler(void *arg) {
  struct signaler *s = arg;
  for (int i = 0; !atomic_load(&stop); i = (i + 1) % s->count) {
    pthread_kill(s->targets[i], SIGUSR1);
    usleep(200);
  }
  return NULL;
}

/* A child sees the code as it was at the fork, possibly with one function half
 * changed, so it only checks that each result is one a version could give.
 */
static void *forker(void *arg) {
  (void)arg;
  while (!atomic_load(&stop)) {
    pid_t child = fork();
    if (child < 0)
      break;
    if (child == 0) {
      for (int n = 0; n < 2000; ++n) {
        int result = ((fn_t)(code + offsets[n % FUNCTIONS]))(n & 0xff);
        if (result < (n & 0xff) || result >= (n & 0xff) + 100)
          _exit(1);
      }
      _exit(0);
    }
    int status = 0;
    if (waitpid(child, &status, 0) != child || !WIFEXITED(status) || WEXITSTATUS(status) != 0)
      atomic_fetch_add(&bad_children, 1);
    atomic_fetch_add(&forks, 1);
    usleep(2000);
  }
  return NULL;
}

static int patch(int index, int version) {
  int value = expected(version);
  if (mode == REMAP) {
    /* Build the page's next image in the other half of the file, then map it
     * over the current one.
     */
    static int half;
    unsigned char image[PAGE];
    memcpy(image, code, PAGE);
    write_body(image, offsets[index], value);
    half ^= 1;
    if (pwrite(memfd, image, PAGE, (off_t)half * PAGE) != PAGE)
      return -1;
    return mmap(code, PAGE, PROT_READ | PROT_EXEC, MAP_PRIVATE | MAP_FIXED, memfd,
                (off_t)half * PAGE) == code
               ? 0
               : -1;
  }
  unsigned char *first = code + (offsets[index] & ~(size_t)(PAGE - 1));
  size_t length = ((offsets[index] + BODY - 1) & ~(size_t)(PAGE - 1)) + PAGE -
                  (offsets[index] & ~(size_t)(PAGE - 1));
  if (mode == WX && mprotect(first, length, PROT_READ | PROT_WRITE | PROT_EXEC) != 0)
    return -1;
  write_body(code, offsets[index], value);
  if (mode == WX && mprotect(first, length, PROT_READ | PROT_EXEC) != 0)
    return -1;
  return 0;
}

int main(int argc, char **argv) {
  mode = -1;
  for (int i = 0; argc > 1 && i < (int)(sizeof(mode_names) / sizeof(mode_names[0])); ++i) {
    if (strcmp(argv[1], mode_names[i]) == 0)
      mode = i;
  }
  long rounds = argc > 2 ? atol(argv[2]) : 3000;
  int threads = argc > 3 ? atoi(argv[3]) : 2;
  if (mode < 0 || rounds <= 0 || rounds > 1000000 || threads < 1 || threads > 16)
    return 2;

  size_t size = mode == REMAP ? PAGE : PAGES * PAGE;
  if (mode == REMAP) {
    memfd = memfd_create("jit-stress", 0);
    if (memfd < 0 || ftruncate(memfd, 2 * PAGE) != 0)
      return 2;
    code = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, memfd, 0);
  } else {
    code = mmap(NULL, size, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  }
  if (code == MAP_FAILED)
    return 2;
  for (int i = 0; i < FUNCTIONS; ++i) {
    /* Spread over the region; the second function's immediate crosses a page. */
    offsets[i] = mode == REMAP ? 64 + (size_t)i * 600 : 64 + (size_t)i * (size - 256) / FUNCTIONS;
    if (i == 1 && mode != REMAP)
      offsets[i] = PAGE - 2;
    write_body(code, offsets[i], expected(0));
  }
  if (mode == REMAP) {
    /* Start from a private executable mapping of the first image. */
    unsigned char *shared = code;
    code = mmap(NULL, PAGE, PROT_READ | PROT_EXEC, MAP_PRIVATE, memfd, 0);
    if (code == MAP_FAILED || munmap(shared, size) != 0)
      return 2;
  } else if (mode == WX && mprotect(code, size, PROT_READ | PROT_EXEC) != 0) {
    return 2;
  }

  pthread_t workers[16], extra;
  struct signaler signal_state = {workers, threads};
  int have_extra = 0;
  if (mode == SIGNAL) {
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = on_signal;
    action.sa_flags = SA_RESTART;
    if (sigaction(SIGUSR1, &action, NULL) != 0)
      return 2;
  }
  for (int i = 0; i < threads; ++i) {
    if (pthread_create(&workers[i], NULL, mode == CHURN ? spawner : worker, NULL) != 0)
      return 2;
  }
  if (mode == SIGNAL || mode == FORK) {
    if (pthread_create(&extra, NULL, mode == SIGNAL ? signaler : forker,
                       mode == SIGNAL ? &signal_state : NULL) != 0)
      return 2;
    have_extra = 1;
  }

  /* Under the runtime a thread can take longer to start than all the rounds
   * take: wait until the workers call the functions (in churn mode, the first
   * short-lived worker). */
  while (atomic_load(&ready) < (mode == CHURN ? 1 : threads))
    sched_yield();

  int failed = 0;
  for (long round = 1; round <= rounds && !failed; ++round) {
    int index = (int)(round % FUNCTIONS);
    int version = atomic_load(&versions[index]);
    atomic_store(&versions[index], version + 1);
    failed = patch(index, version + 2) != 0;
    atomic_store(&versions[index], version + 2);
    /* Vary the pause, so that some changes land while a worker rebuilds. */
    for (volatile int spin = 0; spin < (1000 << (round % 8)); ++spin) {
    }
  }
  atomic_store(&stop, 1);
  for (int i = 0; i < threads; ++i)
    pthread_join(workers[i], NULL);
  if (have_extra)
    pthread_join(extra, NULL);
  printf("{\"mode\":\"%s\",\"rounds\":%ld,\"patch_failed\":%d,\"calls\":%ld,\"stale\":%ld,"
         "\"signals\":%ld,\"threads_created\":%ld,\"forks\":%ld,\"bad_children\":%ld}\n",
         mode_names[mode], rounds, failed, atomic_load(&calls), atomic_load(&stale),
         atomic_load(&signals), atomic_load(&threads_created), atomic_load(&forks),
         atomic_load(&bad_children));
  return failed || atomic_load(&stale) != 0 || atomic_load(&bad_children) != 0;
}

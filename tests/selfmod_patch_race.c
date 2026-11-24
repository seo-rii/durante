/* Generated code patched in place while other threads run it.
 *
 * The main thread keeps patching a generated function on a writable and
 * executable page, as a JIT that leaves its code RWX does, while worker threads
 * call it. A version counter is odd while a patch is in progress, and a call
 * between two reads of the same even version must return that version's result.
 * The runtime sandboxes such a page: each block checks on entry that the code is
 * still what the block was built from. A runtime that took the copy for that
 * check after decoding the block could take it after a patch, and the block then
 * kept running the old code while passing its check.
 *
 * The time between patches varies, so that some patches land while a worker is
 * rebuilding the block, however fast that is.
 *
 * Usage: selfmod_patch_race [ROUNDS [THREADS]]. Prints one JSON line.
 */
#define _GNU_SOURCE
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

typedef int (*fn_t)(int);

static unsigned char *code;
static atomic_int version; /* odd while a patch is in progress */
static atomic_int started, stop;
static atomic_long calls, stale;

static const unsigned char body[] = {
    0x8d, 0x47, 0x00, /* lea eax, [rdi+imm8] */
    0xc3,             /* ret */
};
#define IMM_OFFS 2

static int expected(int v) { return (v / 2) % 100; }

static void *worker(void *arg) {
  (void)arg;
  fn_t fn = (fn_t)code;
  long n = 0, bad = 0;
  while (!atomic_load(&stop)) {
    int v1 = atomic_load(&version);
    if (v1 % 2 != 0)
      continue;
    int res = fn(0);
    if (atomic_load(&version) == v1 && res != expected(v1))
      bad++;
    if (n++ == 0)
      atomic_fetch_add(&started, 1);
  }
  atomic_fetch_add(&calls, n);
  atomic_fetch_add(&stale, bad);
  return NULL;
}

int main(int argc, char **argv) {
  long rounds = argc > 1 ? atol(argv[1]) : 4000;
  int threads = argc > 2 ? atoi(argv[2]) : 2;
  if (rounds <= 0 || rounds > 1000000 || threads < 1 || threads > 16)
    return 2;
  code = mmap(NULL, 4096, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANONYMOUS,
              -1, 0);
  if (code == MAP_FAILED)
    return 2;
  memcpy(code, body, sizeof(body));
  pthread_t tid[16];
  for (int i = 0; i < threads; ++i) {
    if (pthread_create(&tid[i], NULL, worker, NULL) != 0)
      return 2;
  }
  while (atomic_load(&started) < threads) {
    /* Wait until every worker has run the code. */
  }
  for (long i = 1; i <= rounds; ++i) {
    atomic_store(&version, (int)(2 * i - 1));
    code[IMM_OFFS] = (unsigned char)expected((int)(2 * i));
    atomic_store(&version, (int)(2 * i));
    for (volatile int j = 0; j < (1000 << (i % 10)); ++j) {
      /* Let the workers run the patched code. */
    }
  }
  atomic_store(&stop, 1);
  for (int i = 0; i < threads; ++i)
    pthread_join(tid[i], NULL);
  printf("{\"rounds\":%ld,\"calls\":%ld,\"stale\":%ld}\n", rounds, atomic_load(&calls),
         atomic_load(&stale));
  return atomic_load(&stale) != 0;
}

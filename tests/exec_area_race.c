/* Generated code whose page protection changes while other threads run it.
 *
 * Worker threads call a small generated function through a register in a loop
 * and check its result. The main thread keeps making the function's page
 * writable and then executable again (RX -> RWX -> RX), as a JIT that patches
 * under a W^X discipline, without changing the code. Each change removes the
 * page's executable area in the runtime, often right after it was added and
 * before any worker executed from it. A runtime that kept a stale pointer to
 * such an area when a lookup failed crashed here.
 *
 * Usage: exec_area_race [ROUNDS [THREADS]]. Prints one JSON line.
 */
#define _GNU_SOURCE
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

typedef long (*fn_t)(long);

static unsigned char *code;
static atomic_int stop;
static atomic_long calls, wrong;

static void *worker(void *arg) {
  (void)arg;
  fn_t fn = (fn_t)code;
  long n = 0, bad = 0;
  while (!atomic_load_explicit(&stop, memory_order_relaxed)) {
    if (fn(n) != n + 1)
      bad++;
    n++;
  }
  atomic_fetch_add(&calls, n);
  atomic_fetch_add(&wrong, bad);
  return NULL;
}

int main(int argc, char **argv) {
  long rounds = argc > 1 ? atol(argv[1]) : 5000;
  int threads = argc > 2 ? atoi(argv[2]) : 3;
  if (rounds <= 0 || threads < 1 || threads > 16)
    return 2;
  code = mmap(NULL, 4096, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANONYMOUS,
              -1, 0);
  if (code == MAP_FAILED)
    return 2;
  static const unsigned char body[] = {
      0x48, 0x8d, 0x47, 0x01, /* lea rax, [rdi+1] */
      0xc3,                   /* ret */
  };
  memcpy(code, body, sizeof(body));
  if (mprotect(code, 4096, PROT_READ | PROT_EXEC) != 0)
    return 2;
  pthread_t tid[16];
  for (int i = 0; i < threads; ++i) {
    if (pthread_create(&tid[i], NULL, worker, NULL) != 0)
      return 2;
  }
  for (long i = 0; i < rounds; ++i) {
    if (mprotect(code, 4096, PROT_READ | PROT_WRITE | PROT_EXEC) != 0 ||
        mprotect(code, 4096, PROT_READ | PROT_EXEC) != 0)
      return 3;
    for (volatile int j = 0; j < 2000; ++j) {
      /* Let the workers run the code between changes. */
    }
  }
  atomic_store(&stop, 1);
  for (int i = 0; i < threads; ++i)
    pthread_join(tid[i], NULL);
  printf("{\"rounds\":%ld,\"calls\":%ld,\"wrong\":%ld}\n", rounds, atomic_load(&calls),
         atomic_load(&wrong));
  return atomic_load(&wrong) != 0;
}

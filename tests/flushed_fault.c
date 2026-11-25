/* A fault in generated code that another thread's write has just flushed.
 *
 * One thread repeatedly calls a small generated function: a long REP MOVSB,
 * then a load from a "polling page" (as at a JIT's safepoint poll), then RET.
 * Another thread writes to another byte of the same code page, as a JIT that
 * patches a call site, and then arms the polling page with PROT_NONE. The
 * write flushes the block the first thread is still copying in. The poll then
 * faults inside that flushed block, and the handler checks that the reported pc
 * is the poll. A runtime that cannot translate the fault reports another pc,
 * zero in the observed failure (BUG-006).
 *
 * Usage: flushed_fault [ROUNDS [COPY_BYTES]]. Prints one JSON line.
 */
#define _GNU_SOURCE
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <ucontext.h>
#include <unistd.h>

typedef void (*copy_fn)(void *dst, const void *src, size_t n, void *poll);

static unsigned char *code, *poll_page, *poll_pc;
static atomic_int armed, faults, wrong_pc, stop;

static void handler(int sig, siginfo_t *info, void *context) {
  (void)sig;
  ucontext_t *uc = context;
  unsigned char *pc = (unsigned char *)uc->uc_mcontext.gregs[REG_RIP];
  if ((unsigned char *)info->si_addr != poll_page)
    _exit(3);
  if (pc != poll_pc) {
    atomic_fetch_add(&wrong_pc, 1);
    /* Resume after the 3-byte poll so that the run can go on. */
    uc->uc_mcontext.gregs[REG_RIP] = (greg_t)(poll_pc + 3);
  }
  atomic_fetch_add(&faults, 1);
  /* Disarm: the poll executes again. */
  if (mprotect(poll_page, 4096, PROT_READ | PROT_WRITE) != 0)
    _exit(4);
  atomic_store(&armed, 0);
}

static void *patcher(void *arg) {
  (void)arg;
  unsigned seed = 1;
  while (!atomic_load(&stop)) {
    struct timespec pause = {0, 20000 + rand_r(&seed) % 80000};
    nanosleep(&pause, NULL);
    code[2048] ^= 1;
    atomic_store(&armed, 1);
    if (mprotect(poll_page, 4096, PROT_NONE) != 0)
      _exit(5);
    while (atomic_load(&armed) && !atomic_load(&stop)) {
      /* The poll disarms. */
    }
  }
  return NULL;
}

int main(int argc, char **argv) {
  int rounds = argc > 1 ? atoi(argv[1]) : 200;
  size_t copy_bytes = argc > 2 ? (size_t)atol(argv[2]) : (size_t)16 << 20;
  if (rounds <= 0 || copy_bytes == 0)
    return 2;
  code = mmap(NULL, 4096, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANONYMOUS,
              -1, 0);
  poll_page = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  unsigned char *src = calloc(1, copy_bytes), *dst = calloc(1, copy_bytes);
  if (code == MAP_FAILED || poll_page == MAP_FAILED || !src || !dst)
    return 2;
  static const unsigned char body[] = {
      0x49, 0x89, 0xc8, /* mov r8, rcx */
      0x48, 0x89, 0xd1, /* mov rcx, rdx */
      0xf3, 0xa4,       /* rep movsb */
      0x41, 0x85, 0x00, /* test [r8], eax: the poll */
      0xc3,             /* ret */
  };
  memcpy(code, body, sizeof(body));
  poll_pc = code + 8;
  struct sigaction act;
  memset(&act, 0, sizeof(act));
  act.sa_sigaction = handler;
  act.sa_flags = SA_SIGINFO;
  if (sigaction(SIGSEGV, &act, NULL) != 0)
    return 2;
  pthread_t thread;
  if (pthread_create(&thread, NULL, patcher, NULL) != 0)
    return 2;
  copy_fn copy = (copy_fn)code;
  while (atomic_load(&faults) < rounds)
    copy(dst, src, copy_bytes, poll_page);
  atomic_store(&stop, 1);
  pthread_join(thread, NULL);
  printf("{\"faults\":%d,\"wrong_pc\":%d}\n", atomic_load(&faults), atomic_load(&wrong_pc));
  return atomic_load(&wrong_pc) != 0;
}

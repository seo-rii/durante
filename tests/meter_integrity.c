/* Attack fixtures for -protect_meter (tests/meter_integrity.py).
 *
 * Each mode does real work and then tries one route to change Durante's counts.
 * It finds its targets the way an attacker would: /proc/self/maps for the
 * runtime's writable mappings and code cache, /proc/self/fd for the client's
 * record stream. A store-based mode performs a round trip: it saves a word of
 * Durante's memory, writes a sentinel, reads it back, restores the word, and
 * prints a one-line JSON result to stdout. A round trip that reads its own
 * sentinel back proves the route is open; the restore keeps the honest run
 * valid so the demonstration does not depend on crashing the runtime.
 *
 * Without -protect_meter every route is expected to complete ("reached":true).
 * With it, the store or syscall is stopped before it executes, so the process
 * never prints a result and the run is reported as an integrity violation. */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/uio.h>
#include <unistd.h>

extern char **environ;

#define FAIL(...) do { fprintf(stderr, __VA_ARGS__); return 2; } while (0)

static volatile uint64_t g_sink;

/* Real work: enough that an honest run reports far above a lowered count. */
static void work(void) {
  uint64_t s = 0;
  for (uint64_t i = 0; i < 4000000; ++i)
    s += i * 2654435761u + (s >> 3);
  g_sink += s;
}

typedef struct { uintptr_t start, end; char prot[5]; char name[256]; } region_t;

/* Find a runtime mapping. want_name: a substring of the pathname (e.g. the
 * client library) or NULL for an anonymous region; prot is an exact "rwxp"
 * style match. Skips the fixture's own executable, stack and the vdso. */
static int find_region(const char *want_name, const char *prot, bool anon, region_t *out) {
  FILE *maps = fopen("/proc/self/maps", "r");
  if (!maps)
    return -1;
  char line[512];
  int found = -1;
  while (fgets(line, sizeof(line), maps)) {
    region_t r;
    memset(&r, 0, sizeof(r));
    char perms[8] = {0};
    int n = sscanf(line, "%lx-%lx %4s %*x %*x:%*x %*u %255[^\n]", &r.start, &r.end, perms, r.name);
    if (n < 3)
      continue;
    memcpy(r.prot, perms, 4);
    if (strncmp(r.prot, prot, 4) != 0)
      continue;
    if (anon && (n >= 4 && r.name[0] != '\0' && r.name[0] != ' '))
      continue;
    if (anon && strstr(line, "["))
      continue;
    if (want_name && !(n >= 4 && strstr(r.name, want_name)))
      continue;
    if (!want_name && !anon)
      continue;
    *out = r;
    found = 0;
    break;
  }
  fclose(maps);
  return found;
}

/* The client's own record stream appears in /proc/self/fd via the protected
 * descriptor the client keeps open. */
static int find_record(char *out, size_t capacity) {
  DIR *dir = opendir("/proc/self/fd");
  if (!dir)
    return -1;
  struct dirent *entry;
  int found = -1;
  while ((entry = readdir(dir))) {
    char alias[128];
    snprintf(alias, sizeof(alias), "/proc/self/fd/%.64s", entry->d_name);
    ssize_t size = readlink(alias, out, capacity - 1);
    if (size > 0) {
      out[size] = 0;
      if (strstr(out, "/process-") && strstr(out, ".jsonl")) {
        closedir(dir);
        return 0;
      }
    }
  }
  closedir(dir);
  return found;
}

static void reached(const char *mode, bool ok, uint64_t observed) {
  printf("{\"mode\":\"%s\",\"reached\":%s,\"observed\":%llu}\n", mode, ok ? "true" : "false",
         (unsigned long long)observed);
  fflush(stdout);
}

/* Target a writable 8-byte word of Durante's memory. The caller passes the
 * region; we pick a word well inside it. */
static volatile uint64_t *target_word(const region_t *r) {
  uintptr_t addr = r->start + (r->end - r->start) / 2;
  addr &= ~(uintptr_t)7;
  return (volatile uint64_t *)addr;
}

int main(int argc, char **argv) {
  if (argc < 2)
    FAIL("usage: meter_integrity MODE\n");
  const char *mode = argv[1];
  work();

  if (!strcmp(mode, "honest")) {
    reached(mode, true, g_sink & 0xffff);
    return 0;
  }

  /* --- Memory stores into the client's writable data (holds the counters and
   * client globals). The region is named after the client library. --- */
  region_t data;
  bool have_data = find_region("durante", "rw-p", false, &data) == 0;
  region_t code;
  bool have_code = find_region(NULL, "rwxp", true, &code) == 0;

  if (!strcmp(mode, "store-plain")) {
    if (!have_data)
      FAIL("no client data region\n");
    volatile uint64_t *p = target_word(&data);
    uint64_t saved = *p;      /* load: allowed */
    *p = 0xdeadbeefcafef00dULL; /* store into Durante memory: the attack */
    uint64_t back = *p;
    *p = saved;               /* restore so the honest run stays valid */
    reached(mode, back == 0xdeadbeefcafef00dULL, back);
    return 0;
  }
  if (!strcmp(mode, "store-rmw")) {
    if (!have_data)
      FAIL("no client data region\n");
    volatile uint64_t *p = target_word(&data);
    uint64_t saved = *p;
    uint64_t prev = __atomic_fetch_add(p, 1, __ATOMIC_SEQ_CST); /* lock xadd */
    uint64_t swapped = __atomic_exchange_n(p, 0, __ATOMIC_SEQ_CST); /* xchg */
    *p = saved;
    reached(mode, swapped == prev + 1, swapped);
    return 0;
  }
  if (!strcmp(mode, "store-rep")) {
    if (!have_data)
      FAIL("no client data region\n");
    volatile uint64_t *p = target_word(&data);
    uint64_t save[8];
    memcpy(save, (void *)p, sizeof(save));  /* rep movs */
    uint64_t pattern[8];
    memset(pattern, 0x5a, sizeof(pattern)); /* rep stos into Durante memory */
    memcpy((void *)p, pattern, sizeof(pattern));
    uint64_t back = *p;
    memcpy((void *)p, save, sizeof(save));
    reached(mode, back == pattern[0], back);
    return 0;
  }
  if (!strcmp(mode, "store-simd")) {
    if (!have_data)
      FAIL("no client data region\n");
    volatile uint64_t *p = target_word(&data);
    uint64_t saved[2];
    memcpy(saved, (void *)p, sizeof(saved));
    double v[2] = {1.0, 2.0};
    __asm__ __volatile__("movups %%xmm0, (%0)" ::"r"(p), "x"(*(double(*)[2])v) : "memory");
    uint64_t back = *p;
    memcpy((void *)p, saved, sizeof(saved));
    reached(mode, back != saved[0] || true, back);
    return 0;
  }
  if (!strcmp(mode, "store-stack")) {
    if (!have_data)
      FAIL("no client data region\n");
    /* Pivot the stack into Durante memory and let a push write there. */
    volatile uint64_t *p = target_word(&data);
    uint64_t saved = *p;
    uint64_t marker = 0x1122334455667788ULL;
    void *old_sp;
    __asm__ __volatile__(
        "mov %%rsp, %0\n\t"
        "lea 64(%1), %%rsp\n\t"
        "push %2\n\t"
        "mov %0, %%rsp\n\t"
        : "=&r"(old_sp)
        : "r"(p), "r"(marker)
        : "memory");
    uint64_t back = *(volatile uint64_t *)((char *)p + 64 - 8);
    *p = saved;
    reached(mode, back == marker, back);
    return 0;
  }
  if (!strcmp(mode, "store-codecache")) {
    if (!have_code)
      FAIL("no code cache region\n");
    volatile uint8_t *p = (volatile uint8_t *)(code.start + 64);
    uint8_t saved = *p;
    *p = 0x90; /* patch a byte of the code cache */
    uint8_t back = *p;
    *p = saved;
    reached(mode, back == 0x90, back);
    return 0;
  }

  /* --- System calls that write Durante's memory. --- */
  if (!strcmp(mode, "syscall-read")) {
    if (!have_data)
      FAIL("no client data region\n");
    volatile uint64_t *p = target_word(&data);
    uint64_t saved = *p;
    int fd = open("/dev/zero", O_RDONLY);
    if (fd < 0)
      FAIL("open /dev/zero\n");
    ssize_t n = read(fd, (void *)p, sizeof(uint64_t)); /* kernel writes Durante memory */
    close(fd);
    uint64_t back = *p;
    *p = saved;
    reached(mode, n == (ssize_t)sizeof(uint64_t) && back == 0, back);
    return 0;
  }
  if (!strcmp(mode, "syscall-procmem")) {
    if (!have_data)
      FAIL("no client data region\n");
    volatile uint64_t *p = target_word(&data);
    uint64_t saved = *p, sentinel = 0x6d656d2d61747400ULL;
    int fd = open("/proc/self/mem", O_RDWR);
    if (fd < 0)
      FAIL("open /proc/self/mem\n");
    ssize_t n = pwrite(fd, &sentinel, sizeof(sentinel), (off_t)(uintptr_t)p);
    close(fd);
    uint64_t back = *p;
    *p = saved;
    reached(mode, n == (ssize_t)sizeof(sentinel) && back == sentinel, back);
    return 0;
  }
  if (!strcmp(mode, "syscall-processvm")) {
    if (!have_data)
      FAIL("no client data region\n");
    volatile uint64_t *p = target_word(&data);
    uint64_t saved = *p, sentinel = 0x7076777200000000ULL;
    struct iovec local = {&sentinel, sizeof(sentinel)};
    struct iovec remote = {(void *)p, sizeof(sentinel)};
    ssize_t n = syscall(SYS_process_vm_writev, getpid(), &local, 1, &remote, 1, 0);
    uint64_t back = *p;
    *p = saved;
    reached(mode, n == (ssize_t)sizeof(sentinel) && back == sentinel, back);
    return 0;
  }
  if (!strcmp(mode, "syscall-mmap-fixed")) {
    if (!have_code)
      FAIL("no code cache region\n");
    void *addr = (void *)code.start;
    void *r = mmap(addr, 4096, PROT_READ | PROT_WRITE,
                   MAP_FIXED | MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    reached(mode, r == addr, (uint64_t)(uintptr_t)r);
    return 0;
  }
  if (!strcmp(mode, "syscall-madvise")) {
    if (!have_data)
      FAIL("no client data region\n");
    /* MADV_DONTNEED on the client's writable data discards its live counters;
     * the next access reloads the pristine file image. */
    int r = madvise((void *)data.start, 4096, MADV_DONTNEED);
    reached(mode, r == 0, (uint64_t)r);
    return 0;
  }
  if (!strcmp(mode, "syscall-mprotect")) {
    if (!have_data)
      FAIL("no client data region\n");
    int r = mprotect((void *)data.start, 4096, PROT_NONE); /* make the counter unreadable */
    reached(mode, r == 0, (uint64_t)r);
    return 0;
  }

  /* --- Files: rewrite the client's own record stream. --- */
  if (!strcmp(mode, "file-records")) {
    char path[PATH_MAX];
    if (find_record(path, sizeof(path)) != 0)
      FAIL("no record stream\n");
    int fd = open(path, O_RDWR | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0)
      FAIL("open record\n");
    char buf[8192];
    ssize_t n = read(fd, buf, sizeof(buf) - 1);
    close(fd);
    reached(mode, n > 0, (uint64_t)n);
    return 0;
  }

  /* --- Files: plant a forged final report through a relative path. The
   * supervisor holds the report open; without protection it refuses to
   * publish over the change, but the forged bytes stay in the file. --- */
  if (!strcmp(mode, "file-report")) {
    int fd = open(argc > 2 ? argv[2] : "report.json", O_RDWR | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0)
      FAIL("open report\n");
    static const char forged[] = "{\"measurement\":{\"valid\":true,\"complete\":true,"
                                 "\"instructions\":1}}\n";
    ssize_t n = write(fd, forged, sizeof(forged) - 1);
    close(fd);
    reached(mode, n == (ssize_t)(sizeof(forged) - 1), (uint64_t)n);
    return 0;
  }

  FAIL("unknown mode %s\n", mode);
}

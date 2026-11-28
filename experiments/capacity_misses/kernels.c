/* Calibration kernels for the capacity-miss study. Each mode reads its sizes
 * from argv, does a fixed amount of work and prints a checksum, so outputs
 * pin the work done.
 *
 *   int N                integer multiply-add chain
 *   branch N             data-dependent branches over a 4 KiB table
 *   read BYTES N         N independent xorshift-indexed loads over BYTES
 *   chase BYTES N        N dependent loads around a random cycle over BYTES
 *   scan BYTES N         N sequential 8-byte loads, wrapping over BYTES
 *   store BYTES N        N xorshift-indexed 8-byte stores over BYTES
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint32_t xs = 2463534242u;
static inline uint32_t next32(void) {
  xs ^= xs << 13;
  xs ^= xs >> 17;
  xs ^= xs << 5;
  return xs;
}

static uint64_t *table(size_t bytes) {
  size_t n = bytes / sizeof(uint64_t);
  uint64_t *t = aligned_alloc(4096, n * sizeof(uint64_t));
  if (!t)
    exit(3);
  for (size_t i = 0; i < n; ++i)
    t[i] = i * 0x9E3779B97F4A7C15ull;
  return t;
}

int main(int argc, char **argv) {
  if (argc < 3)
    return 2;
  const char *mode = argv[1];
  uint64_t sum = 0;
  if (!strcmp(mode, "int")) {
    uint64_t n = strtoull(argv[2], NULL, 10), x = 1;
    for (uint64_t i = 0; i < n; ++i)
      x = x * 6364136223846793005ull + i;
    sum = x;
  } else if (!strcmp(mode, "branch")) {
    uint64_t n = strtoull(argv[2], NULL, 10);
    static uint8_t bits[4096];
    for (unsigned i = 0; i < sizeof(bits); ++i)
      bits[i] = next32() & 1;
    for (uint64_t i = 0; i < n; ++i) {
      if (bits[(i * 2654435761u) & 4095])
        sum += i;
      else
        sum ^= i;
    }
  } else if (argc >= 4) {
    size_t bytes = strtoull(argv[2], NULL, 10);
    uint64_t n = strtoull(argv[3], NULL, 10);
    size_t entries = bytes / sizeof(uint64_t), mask = entries - 1;
    if (!entries || (entries & mask))
      return 2;
    uint64_t *t = table(bytes);
    if (!strcmp(mode, "read")) {
      for (uint64_t i = 0; i < n; ++i)
        sum += t[next32() & mask];
    } else if (!strcmp(mode, "chase")) {
      /* Sattolo's algorithm gives one cycle through every entry. */
      for (size_t i = 0; i < entries; ++i)
        t[i] = i;
      for (size_t i = entries - 1; i > 0; --i) {
        size_t j = (((uint64_t)next32() << 32) | next32()) % i;
        uint64_t tmp = t[i];
        t[i] = t[j];
        t[j] = tmp;
      }
      uint64_t p = 0;
      for (uint64_t i = 0; i < n; ++i)
        p = t[p];
      sum = p;
    } else if (!strcmp(mode, "scan")) {
      size_t j = 0;
      for (uint64_t i = 0; i < n; ++i) {
        sum += t[j];
        j = (j + 1) & mask;
      }
    } else if (!strcmp(mode, "store")) {
      for (uint64_t i = 0; i < n; ++i)
        t[next32() & mask] = i;
      for (size_t i = 0; i < entries; ++i)
        sum += t[i];
    } else {
      return 2;
    }
  } else {
    return 2;
  }
  printf("%llu\n", (unsigned long long)sum);
  return 0;
}

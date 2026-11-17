/* Prints the CPU identity a program observes and does glibc string work whose
 * implementation is selected from CPUID at startup. */
#include <cpuid.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void leaf(unsigned int number, unsigned int sub, int *first) {
  unsigned int a, b, c, d;
  __cpuid_count(number, sub, a, b, c, d);
  printf("%s\"%#x.%u\":[%u,%u,%u,%u]", *first ? "" : ",", number, sub, a, b, c, d);
  *first = 0;
}

int main(int argc, char **argv) {
  int rounds = argc > 1 ? atoi(argv[1]) : 8;
  static const unsigned int leaves[][2] = {
      {0, 0}, {1, 0}, {2, 0}, {4, 0}, {7, 0}, {7, 1}, {0xb, 0}, {0xd, 0}, {0xd, 1},
      {0x40000000u, 0}, {0x80000000u, 0}, {0x80000001u, 0}, {0x80000002u, 0},
      {0x80000003u, 0}, {0x80000004u, 0}, {0x80000006u, 0}, {0x80000008u, 0}};
  printf("{\"cpuid\":{");
  int first = 1;
  for (size_t i = 0; i < sizeof(leaves) / sizeof(leaves[0]); ++i)
    leaf(leaves[i][0], leaves[i][1], &first);
  unsigned int lo, hi;
  /* Executed unconditionally: the host enables XSAVE, and the profile must
   * return a fixed XCR0 instead of the host's register. */
  __asm__ volatile("xgetbv" : "=a"(lo), "=d"(hi) : "c"(0));
  __builtin_cpu_init();
  printf("},\"xcr0\":%llu,\"supports\":{\"sse2\":%d,\"sse4.2\":%d,\"popcnt\":%d,"
         "\"avx\":%d,\"avx2\":%d,\"avx512f\":%d},\"is_intel\":%d,\"is_amd\":%d",
         ((unsigned long long)hi << 32) | lo, !!__builtin_cpu_supports("sse2"),
         !!__builtin_cpu_supports("sse4.2"), !!__builtin_cpu_supports("popcnt"),
         !!__builtin_cpu_supports("avx"), !!__builtin_cpu_supports("avx2"),
         !!__builtin_cpu_supports("avx512f"), !!__builtin_cpu_is("intel"),
         !!__builtin_cpu_is("amd"));
  size_t size = 1 << 20;
  unsigned char *a = malloc(size), *b = malloc(size);
  if (!a || !b)
    return 2;
  uint64_t sum = 0;
  for (int r = 0; r < rounds; ++r) {
    memset(a, 'a' + r, size - 1);
    a[size - 1] = 0;
    memcpy(b, a, size);
    sum += strlen((char *)b) + (unsigned char)b[r * 4099 % (size - 1)];
    sum += memcmp(a, b, size) == 0;
  }
  printf(",\"checksum\":%llu}\n", (unsigned long long)sum);
  free(a);
  free(b);
  return 0;
}

#include <cpuid.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

/* This executable is compiled for baseline x86-64, not for the ISA under test.
 * XGETBV is legal only after CPUID says the CPU and OS enabled XSAVE support. */
int main(void) {
  unsigned int maximum = __get_cpuid_max(0, NULL);
  unsigned int a = 0, b = 0, c = 0, d = 0;
  unsigned int leaf1_c = 0, leaf1_d = 0, leaf7_b = 0;
  uint64_t xcr0 = 0;
  bool xgetbv_checked = false;
  if (maximum >= 1 && __get_cpuid(1, &a, &b, &c, &d)) {
    leaf1_c = c;
    leaf1_d = d;
    if ((c & bit_XSAVE) && (c & bit_OSXSAVE)) {
      unsigned int low, high;
      __asm__ volatile("xgetbv" : "=a"(low), "=d"(high) : "c"(0));
      xcr0 = ((uint64_t)high << 32) | low;
      xgetbv_checked = true;
    }
  }
  if (maximum >= 7) {
    __cpuid_count(7, 0, a, b, c, d);
    leaf7_b = b;
  }
  bool sse2 = (leaf1_d & bit_SSE2) != 0;
  bool avx = (leaf1_c & bit_AVX) && xgetbv_checked && (xcr0 & 6) == 6;
  bool avx2 = avx && (leaf7_b & bit_AVX2);
  bool avx512 = avx && (leaf7_b & bit_AVX512F) && (xcr0 & 0xe6) == 0xe6;
  printf("{\"maximum_basic_leaf\":%u,\"leaf1_ecx\":%u,\"leaf1_edx\":%u,"
         "\"leaf7_ebx\":%u,\"xgetbv_checked\":%s,\"xcr0\":%" PRIu64 ","
         "\"scalar\":true,\"sse2\":%s,\"avx2\":%s,\"avx512\":%s}\n",
         maximum, leaf1_c, leaf1_d, leaf7_b,
         xgetbv_checked ? "true" : "false", xcr0,
         sse2 ? "true" : "false", avx2 ? "true" : "false",
         avx512 ? "true" : "false");
  return 0;
}

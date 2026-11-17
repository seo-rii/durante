/* Host-independent checks for the fixed CPUID table. Built and run by
 * tests/test_cpuid_profile.py with warnings as errors. */
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include "../client/cpuid_profile.h"

static int failures;
#define CHECK(condition)                                                     \
  do {                                                                       \
    if (!(condition)) {                                                      \
      fprintf(stderr, "%s:%d: check failed: %s\n", __FILE__, __LINE__,      \
              #condition);                                                   \
      ++failures;                                                            \
    }                                                                        \
  } while (0)

static void expect(enum durante_cpuid_profile p, uint32_t leaf, uint32_t subleaf,
                   uint32_t a, uint32_t b, uint32_t c, uint32_t d) {
  uint32_t out[4] = {0xdeadbeef, 0xdeadbeef, 0xdeadbeef, 0xdeadbeef};
  CHECK(durante_cpuid_lookup(p, leaf, subleaf, out));
  if (out[0] != a || out[1] != b || out[2] != c || out[3] != d) {
    fprintf(stderr, "profile %d leaf %#x.%u: got %08x %08x %08x %08x\n", (int)p, leaf,
            subleaf, out[0], out[1], out[2], out[3]);
    ++failures;
  }
}

int main(void) {
  const enum durante_cpuid_profile profiles[] = {DURANTE_CPUID_X86_64_V1,
                                                 DURANTE_CPUID_X86_64_V2};
  CHECK(durante_cpuid_profile_parse("x86-64-v1") == DURANTE_CPUID_X86_64_V1);
  CHECK(durante_cpuid_profile_parse("x86-64-v2") == DURANTE_CPUID_X86_64_V2);
  CHECK(durante_cpuid_profile_parse("x86-64-v3") == DURANTE_CPUID_NONE);
  CHECK(durante_cpuid_profile_parse("") == DURANTE_CPUID_NONE);
  CHECK(durante_cpuid_profile_parse(NULL) == DURANTE_CPUID_NONE);
  CHECK(strcmp(durante_cpuid_profile_name(DURANTE_CPUID_X86_64_V1), "x86-64-v1") == 0);
  CHECK(durante_cpuid_profile_name(DURANTE_CPUID_NONE) == NULL);
  uint32_t out[4];
  CHECK(!durante_cpuid_lookup(DURANTE_CPUID_NONE, 0, 0, out));

  for (size_t i = 0; i < 2; ++i) {
    enum durante_cpuid_profile p = profiles[i];
    bool v2 = p == DURANTE_CPUID_X86_64_V2;
    /* "GenuineIntel" in EBX, EDX, ECX order. */
    expect(p, 0, 0, 7, 0x756e6547, 0x6c65746e, 0x49656e69);
    expect(p, 1, 0, 0x600, 0x00010800, v2 ? 0x00982201 : 0, 0x07888111);
    for (uint32_t leaf = 2; leaf <= 7; ++leaf)
      for (uint32_t sub = 0; sub < 3; ++sub)
        expect(p, leaf, sub, 0, 0, 0, 0);
    /* No XSAVE leaf, topology, hypervisor or out-of-range data. */
    expect(p, 0xd, 0, 0, 0, 0, 0);
    expect(p, 0xd, 1, 0, 0, 0, 0);
    expect(p, 0xb, 0, 0, 0, 0, 0);
    expect(p, 0x40000000u, 0, 0, 0, 0, 0);
    expect(p, 0x80000000u, 0, 0x80000008u, 0, 0, 0);
    expect(p, 0x80000001u, 0, 0, 0, v2 ? 1 : 0, 0x20100800);
    expect(p, 0x80000005u, 0, 0, 0, 0, 0);
    expect(p, 0x80000008u, 0, 0x3030, 0, 0, 0);
    expect(p, 0x80000009u, 0, 0, 0, 0, 0);
    expect(p, 0xc0000000u, 0, 0, 0, 0, 0);
    char brand[49] = {0};
    for (uint32_t leaf = 0x80000002u; leaf <= 0x80000004u; ++leaf) {
      CHECK(durante_cpuid_lookup(p, leaf, 0, out));
      memcpy(brand + (leaf - 0x80000002u) * 16, out, 16);
    }
    CHECK(strcmp(brand, v2 ? "Durante virtual CPU x86-64-v2"
                           : "Durante virtual CPU x86-64-v1") == 0);
    /* OSXSAVE (leaf 1 ECX bit 27), AVX (28), hypervisor (31) stay clear. */
    CHECK(durante_cpuid_lookup(p, 1, 0, out));
    CHECK((out[2] & (UINT32_C(1) << 27)) == 0);
    CHECK((out[2] & (UINT32_C(1) << 28)) == 0);
    CHECK((out[2] & (UINT32_C(1) << 31)) == 0);
    /* Subleaf input never changes a fixed leaf. */
    uint32_t again[4];
    CHECK(durante_cpuid_lookup(p, 1, 123456, again));
    CHECK(memcmp(out, again, sizeof(out)) == 0);

    uint32_t eax = 7, edx = 7;
    CHECK(durante_xgetbv_lookup(p, 0, &eax, &edx) && eax == 3 && edx == 0);
    CHECK(durante_xgetbv_lookup(p, 1, &eax, &edx) && eax == 3 && edx == 0);
    CHECK(!durante_xgetbv_lookup(p, 2, &eax, &edx));
    CHECK(!durante_xgetbv_lookup(p, 0xffffffffu, &eax, &edx));

    /* Host qualification: all reported ISA bits must be present. */
    uint32_t ecx1 = v2 ? 0x00982201 : 0, edx1 = 0x07888111;
    CHECK(durante_cpuid_host_missing(p, 0x80000008u, ecx1, edx1, v2 ? 1 : 0,
                                     0x20100800) == NULL);
    CHECK(durante_cpuid_host_missing(p, 0x80000008u, 0xffffffffu, 0xffffffffu,
                                     0xffffffffu, 0xffffffffu) == NULL);
    CHECK(durante_cpuid_host_missing(p, 0x80000008u, ecx1, edx1 & ~(UINT32_C(1) << 26),
                                     v2 ? 1 : 0, 0x20100800) != NULL);
    CHECK(durante_cpuid_host_missing(p, 0x80000000u, ecx1, edx1, v2 ? 1 : 0,
                                     0x20100800) != NULL);
    CHECK(durante_cpuid_host_missing(p, 0x80000008u, ecx1, edx1, v2 ? 1 : 0,
                                     0x20100800 & ~(UINT32_C(1) << 29)) != NULL);
    if (v2) {
      CHECK(durante_cpuid_host_missing(p, 0x80000008u, ecx1 & ~(UINT32_C(1) << 20), edx1,
                                       1, 0x20100800) != NULL);
      CHECK(durante_cpuid_host_missing(p, 0x80000008u, ecx1, edx1, 0, 0x20100800) != NULL);
    } else {
      /* v1 does not require SSE3 or LAHF. */
      CHECK(durante_cpuid_host_missing(p, 0x80000008u, 0, edx1, 0, 0x20100800) == NULL);
    }
  }
  CHECK(durante_cpuid_host_missing(DURANTE_CPUID_NONE, 0x80000008u, ~0u, ~0u, ~0u, ~0u) != NULL);
  if (failures)
    return 1;
  puts("ok");
  return 0;
}

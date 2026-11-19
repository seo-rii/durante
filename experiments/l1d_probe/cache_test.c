/* Compare the fast model to a literal per-element replay with an independent
 * least-recently-used implementation. Run with a standard C11 compiler. */
#include "cache.h"
#undef NDEBUG /* Always check reference equivalence, including Release builds. */
#include <assert.h>
#include <stdio.h>

struct slot {
  uintptr_t line;
  uint64_t last_use; /* zero = empty */
};
struct reference {
  struct slot sets[L1D_SETS][L1D_WAYS];
  uint64_t refs, lines, misses;
};

static void ref_line(struct reference *ref, uintptr_t line) {
  struct slot *set = ref->sets[line % L1D_SETS];
  struct slot *oldest = set;
  ++ref->lines;
  for (unsigned i = 0; i < L1D_WAYS; ++i) {
    if (set[i].last_use && set[i].line == line) {
      set[i].last_use = ref->lines;
      return;
    }
    if (set[i].last_use < oldest->last_use)
      oldest = &set[i];
  }
  ++ref->misses;
  oldest->line = line;
  oldest->last_use = ref->lines;
}

static void ref_access(struct reference *ref, uintptr_t addr, unsigned bytes) {
  uintptr_t line = addr / L1D_LINE_BYTES;
  uintptr_t end = (addr + bytes - 1) / L1D_LINE_BYTES;
  ++ref->refs;
  do {
    ref_line(ref, line);
  } while (line++ < end);
}

static void verify(const l1d_cache_t *cache, const struct reference *ref) {
  assert(cache->refs == ref->refs);
  assert(cache->lines == ref->lines);
  assert(cache->misses == ref->misses);
  for (unsigned s = 0; s < L1D_SETS; ++s) {
    uint64_t previous = UINT64_MAX;
    for (unsigned i = 0; i < L1D_WAYS; ++i) {
      const struct slot *next = NULL;
      for (unsigned j = 0; j < L1D_WAYS; ++j) {
        const struct slot *candidate = &ref->sets[s][j];
        if (candidate->last_use && candidate->last_use < previous &&
            (!next || candidate->last_use > next->last_use))
          next = candidate;
      }
      assert(cache->tags[s][i] == (next ? next->line + 1 : 0));
      previous = next ? next->last_use : 0;
    }
  }
}

static void compare_repeat(l1d_cache_t *cache, struct reference *ref, bool movs,
                           uintptr_t src, uintptr_t dst, uint64_t count,
                           unsigned bytes, bool backwards, bool addr32) {
  l1d_repeat(cache, movs, src, dst, count, bytes, backwards, addr32);
  if (addr32)
    count = (uint32_t)count;
  uintptr_t step = backwards ? -(uintptr_t)bytes : bytes;
  for (uint64_t i = 0; i < count; ++i) {
    if (movs)
      ref_access(ref, addr32 ? (uint32_t)src : src, bytes);
    ref_access(ref, addr32 ? (uint32_t)dst : dst, bytes);
    src += step;
    dst += step;
  }
  verify(cache, ref);
}

static uint32_t random_u32(uint32_t *state) {
  *state = *state * 1664525u + 1013904223u;
  return *state;
}

int main(void) {
  l1d_cache_t cache = {0};
  struct reference ref = {0};
  uint32_t rng = 8425613;
  /* More congruent addresses than associativity forces evictions. Repeated
   * cross-line operands must count every line even when both lines hit. */
  for (unsigned n = 0; n < 80; ++n) {
    uintptr_t addr = 63 + (n % 10) * 4096;
    l1d_access_twice(&cache, addr, 8);
    ref_access(&ref, addr, 8);
    ref_access(&ref, addr, 8);
  }
  verify(&cache, &ref);

  for (unsigned bytes = 1; bytes <= 8; bytes *= 2) {
    for (unsigned flags = 0; flags < 8; ++flags) {
      bool movs = flags & 1, backwards = flags & 2, addr32 = flags & 4;
      /* Equal operands, two lines in one set, partially overlapping ranges,
       * aligned and straddling elements, and address-size wrapping. */
      compare_repeat(&cache, &ref, movs, 0xabcd, 0xabcd, 0, bytes,
                     backwards, addr32);
      compare_repeat(&cache, &ref, movs, 0x2000, 0x2000, 257, bytes,
                     backwards, addr32);
      compare_repeat(&cache, &ref, movs, 0x100003f, 0x100103f, 17, bytes,
                     backwards, addr32);
      compare_repeat(&cache, &ref, movs, 0x14001, 0x14003, 213, bytes,
                     backwards, addr32);
      compare_repeat(&cache, &ref, movs, UINT32_MAX - 32u, 13, 239, bytes,
                     backwards, addr32);
    }
  }
  compare_repeat(&cache, &ref, true, UINT32_MAX, 0, UINT64_C(0x100000003), 4,
                 false, true);
  compare_repeat(&cache, &ref, false, 0, 0, 131072, 1, false, false);

  for (unsigned i = 0; i < 3000; ++i) {
    uintptr_t src = random_u32(&rng) % 0x100000;
    uintptr_t dst = random_u32(&rng) % 0x100000;
    unsigned bytes = 1u << (random_u32(&rng) >> 30);
    /* Independent starting addresses exercise alternating cache sets;
     * following scalar accesses retain and check the post-REP state. */
    unsigned flags = random_u32(&rng) >> 29;
    unsigned count = random_u32(&rng) % 96;
    compare_repeat(&cache, &ref, flags & 1, src, dst, count, bytes,
                   flags & 2, flags & 4);
    unsigned scalar_size = 1 + random_u32(&rng) % L1D_LINE_BYTES;
    l1d_access(&cache, src, scalar_size);
    ref_access(&ref, src, scalar_size);
    l1d_access_twice(&cache, dst, scalar_size);
    ref_access(&ref, dst, scalar_size);
    ref_access(&ref, dst, scalar_size);
  }
  verify(&cache, &ref);
  puts("L1D reference counts and complete LRU order match");
  return 0;
}

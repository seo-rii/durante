/* Compare the fast model to a literal per-element replay with an independent
 * least-recently-used implementation. Run with a standard C11 compiler. */
#include "cache.h"
#undef NDEBUG /* Always check reference equivalence, including Release builds. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* A literal LRU stack for the outer capacities: the position of a line is its
 * stack distance, and stream membership uses timestamps instead of an ordered
 * list. */
enum { REF_OUTER_MAX = 1200 };
struct ref_outer {
  uint32_t capacity[OUTER_LEVELS];
  uintptr_t stack[REF_OUTER_MAX];
  uint32_t depth;
  uint64_t accesses, misses[OUTER_LEVELS], stream[OUTER_LEVELS], clock;
  struct {
    uintptr_t line;
    uint64_t time;
  } recent[OUTER_LEVELS][OUTER_STREAMS];
  unsigned used[OUTER_LEVELS];
};

static void ref_outer_miss(struct ref_outer *o, unsigned level, uintptr_t line) {
  ++o->misses[level];
  ++o->clock;
  int best = -1;
  for (unsigned s = 0; s < o->used[level]; ++s) {
    uintptr_t last = o->recent[level][s].line;
    uintptr_t distance = line > last ? line - last : last - line;
    if (distance >= 1 && distance <= OUTER_STREAM_RANGE &&
        (best < 0 || o->recent[level][s].time > o->recent[level][best].time))
      best = (int)s;
  }
  if (best >= 0) {
    ++o->stream[level];
  } else if (o->used[level] < OUTER_STREAMS) {
    best = (int)o->used[level]++;
  } else {
    best = 0;
    for (unsigned s = 1; s < OUTER_STREAMS; ++s)
      if (o->recent[level][s].time < o->recent[level][best].time)
        best = (int)s;
  }
  o->recent[level][best].line = line;
  o->recent[level][best].time = o->clock;
}

static void ref_outer_access(struct ref_outer *o, uintptr_t line) {
  ++o->accesses;
  /* An absent line has an infinite stack distance. */
  uint64_t distance = UINT64_MAX;
  for (uint32_t i = 0; i < o->depth; ++i) {
    if (o->stack[i] == line) {
      distance = i;
      break;
    }
  }
  for (unsigned level = 0; level < OUTER_LEVELS; ++level)
    if (distance >= o->capacity[level])
      ref_outer_miss(o, level, line);
  uint32_t largest = o->capacity[OUTER_LEVELS - 1];
  bool found = distance != UINT64_MAX;
  uint32_t moved = found ? (uint32_t)distance : (o->depth < largest ? o->depth : largest - 1);
  memmove(&o->stack[1], &o->stack[0], moved * sizeof(o->stack[0]));
  o->stack[0] = line;
  if (!found && o->depth < largest)
    ++o->depth;
}

static void verify_outer(const outer_cache_t *cache, const struct ref_outer *o) {
  assert(cache->accesses == o->accesses);
  assert(cache->count == o->depth);
  for (unsigned level = 0; level < OUTER_LEVELS; ++level) {
    assert(cache->misses[level] == o->misses[level]);
    assert(cache->stream_misses[level] == o->stream[level]);
    assert(cache->boundary[level] ==
           (o->depth >= cache->capacity[level] ? cache->boundary[level] : OUTER_NIL));
  }
  uint32_t node = cache->head, previous = OUTER_NIL;
  for (uint32_t i = 0; i < o->depth; ++i) {
    assert(node != OUTER_NIL && cache->line[node] == o->stack[i]);
    assert(cache->prev[node] == previous);
    assert(outer_find(cache, o->stack[i]) == node);
    unsigned zone = 0;
    while (zone < OUTER_LEVELS && i >= cache->capacity[zone])
      ++zone;
    assert(cache->zone[node] == zone);
    for (unsigned level = 0; level < OUTER_LEVELS; ++level)
      if (i + 1 == cache->capacity[level])
        assert(cache->boundary[level] == node);
    previous = node;
    node = cache->next[node];
  }
  assert(node == OUTER_NIL && cache->tail == previous);
}

struct slot {
  uintptr_t line;
  uint64_t last_use; /* zero = empty */
};
struct reference {
  struct slot sets[L1D_SETS][L1D_WAYS];
  uint64_t refs, lines, misses;
  struct ref_outer *outer;
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
  if (ref->outer)
    ref_outer_access(ref->outer, line);
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
  assert(!cache->outer == !ref->outer);
  if (cache->outer)
    verify_outer(cache->outer, ref->outer);
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

/* Every L1 test also feeds outer capacities just above the L1's 512 lines. */
static const uint32_t test_capacity[OUTER_LEVELS] = {520, 700, 1100};

static outer_cache_t *new_outer(const uint32_t capacity[OUTER_LEVELS]) {
  outer_cache_t *outer = calloc(1, sizeof(*outer));
  void *storage = calloc(1, outer_storage_bytes(capacity[OUTER_LEVELS - 1]));
  assert(outer && storage && outer_init(outer, capacity, storage));
  return outer;
}

static struct ref_outer *new_ref_outer(const uint32_t capacity[OUTER_LEVELS]) {
  struct ref_outer *o = calloc(1, sizeof(*o));
  assert(o && capacity[OUTER_LEVELS - 1] <= REF_OUTER_MAX);
  memcpy(o->capacity, capacity, sizeof(o->capacity));
  return o;
}

static void attach_outer(l1d_cache_t *cache, struct reference *ref) {
  cache->outer = new_outer(test_capacity);
  ref->outer = new_ref_outer(test_capacity);
}

static void free_outer(l1d_cache_t *cache, struct reference *ref) {
  free(cache->outer->line); /* the storage block starts with the line array */
  free(cache->outer);
  free(ref->outer);
}

static uint32_t random_u32(uint32_t *state) {
  *state = *state * 1664525u + 1013904223u;
  return *state;
}

static void test_large_stos(void) {
  l1d_cache_t cache = {0};
  struct reference ref = {0};
  attach_outer(&cache, &ref);
  const unsigned capacity = L1D_SETS * L1D_WAYS * L1D_LINE_BYTES;
  for (unsigned bytes = 1; bytes <= 8; bytes *= 2) {
    for (unsigned mode = 0; mode < 8; ++mode) {
      bool backwards = mode & 1, addr32 = mode & 2;
      uintptr_t base = 0x200000 + ((mode & 4) ? 63 : 0);
      unsigned edge = capacity / bytes;
      const unsigned counts[] = {edge - 1, edge, edge + 1, edge + 3, 4 * edge};
      /* Warm both the start of the stream and distant sites that alias its
       * sets. Simulations retain the full cache between cases. */
      for (unsigned i = 0; i < L1D_SETS * L1D_WAYS; ++i) {
        uintptr_t addr = base + (backwards ? capacity - 1 - i * L1D_LINE_BYTES
                                           : i * L1D_LINE_BYTES);
        if (i % 3 == 0)
          addr += 2 * capacity;
        l1d_access(&cache, addr, 1);
        ref_access(&ref, addr, 1);
      }
      for (unsigned i = 0; i < sizeof(counts) / sizeof(counts[0]); ++i) {
        uintptr_t dst = backwards ? base + (counts[i] - 1) * bytes : base;
        if (addr32)
          dst += UINT64_C(1) << 40;
        compare_repeat(&cache, &ref, false, 0, dst, counts[i], bytes,
                       backwards, addr32);
      }
    }
  }
  /* Equal-to-limit and wrapping spans in both address modes and directions. */
  for (unsigned addr32 = 0; addr32 < 2; ++addr32) {
    uintptr_t max = addr32 ? UINT32_MAX : UINTPTR_MAX;
    unsigned count = capacity / 8 + 1;
    compare_repeat(&cache, &ref, false, 0, max - count * 8 + 1, count, 8, false, addr32);
    compare_repeat(&cache, &ref, false, 0, max - 3, count, 8, false, addr32);
    compare_repeat(&cache, &ref, false, 0, (count - 1) * 8, count, 8, true, addr32);
    compare_repeat(&cache, &ref, false, 0, 3, count, 8, true, addr32);
  }
  free_outer(&cache, &ref);
}

static void scalar_pair(l1d_cache_t *cache, struct reference *ref,
                        uintptr_t addr, bool addr32) {
  if (addr32)
    addr = (uint32_t)addr;
  l1d_access(cache, addr, 1);
  ref_access(ref, addr, 1);
}

/* Each case starts with tags from both streams near the first, middle and
 * last accesses. Verify every counter and way before and after scalar reuse. */
static void compare_warm_movs(uintptr_t src, uintptr_t dst, uint64_t count,
                              unsigned bytes, bool backwards, bool addr32) {
  l1d_cache_t cache = {0};
  struct reference ref = {0};
  attach_outer(&cache, &ref);
  uintptr_t n = addr32 ? (uint32_t)count : count;
  uintptr_t end = (n - 1) * bytes;
  for (unsigned i = 0; i < L1D_SETS; ++i) {
    uintptr_t delta = i * L1D_LINE_BYTES;
    const uintptr_t positions[] = {delta, end / 2 + delta, end - delta};
    for (unsigned j = 0; j < 3; ++j) {
      uintptr_t step = backwards ? -positions[j] : positions[j];
      scalar_pair(&cache, &ref, src + step, addr32);
      scalar_pair(&cache, &ref, dst + step, addr32);
    }
  }
  compare_repeat(&cache, &ref, true, src, dst, count, bytes, backwards, addr32);
  uintptr_t step = backwards ? -end : end;
  scalar_pair(&cache, &ref, dst + step, addr32);
  scalar_pair(&cache, &ref, src + step, addr32);
  scalar_pair(&cache, &ref, dst, addr32);
  scalar_pair(&cache, &ref, src, addr32);
  verify(&cache, &ref);
  free_outer(&cache, &ref);
}

static void test_large_movs(void) {
  const unsigned line = L1D_LINE_BYTES;
  const unsigned capacity = L1D_SETS * L1D_WAYS * line;
  const unsigned span = 3 * capacity;
  const uintptr_t base = 0x200000;
  const uintptr_t distant = base + 0x80000;
  for (unsigned direction = 0; direction < 2; ++direction) {
    bool backwards = direction != 0;
    for (unsigned bytes = 1; bytes <= 8; bytes *= 2) {
      /* The last source cache line neighbors the first destination line.
       * The odd gap in lines gives the streams different set indices. */
      unsigned adjacent_span = span + 5 * line;
      uintptr_t start = base + (backwards ? adjacent_span - bytes : 0);
      compare_warm_movs(start, start + adjacent_span, adjacent_span / bytes,
                        bytes, backwards, false);
      /* Same-set streams, partial endpoint lines, nonzero high address and
       * count bits discarded for addr32. */
      uint64_t count32 = (UINT64_C(1) << 32) + span / bytes + 3;
      compare_warm_movs((UINT64_C(1) << 40) + base + 16,
                        (UINT64_C(3) << 40) + distant + 16,
                        count32, bytes, backwards, true);
    }
    /* Cross the prefix boundary with every relative set position, changing
     * which stream supplies the newest tag for each set. */
    for (unsigned gap = 0; gap < L1D_SETS; ++gap) {
      for (unsigned extra = 0; extra <= 2; ++extra) {
        unsigned length = capacity / 2 - line + extra * line;
        uintptr_t start = backwards ? length - 8 : 0;
        compare_warm_movs(distant + start + gap * line, base + start,
                          length / 8, 8, backwards, false);
      }
    }
    /* Disjoint bytes can still share an endpoint line. Other cases revisit
     * overlapping streams in both orders, or have unequal line offsets or
     * elements that straddle lines. */
    compare_warm_movs(base + 8, base + span + 8, span / 8, 8, backwards, false);
    compare_warm_movs(base, base + line, span / 8, 8, backwards, false);
    compare_warm_movs(base + line, base, span / 8, 8, backwards, false);
    compare_warm_movs(base + 8, distant + 16, span / 8, 8, backwards, false);
    compare_warm_movs(base + 63, distant + 63, span / 8, 8, backwards, false);
    for (unsigned addr32 = 0; addr32 < 2; ++addr32) {
      uintptr_t limit = addr32 ? UINT32_MAX : UINTPTR_MAX;
      uintptr_t near_wrap = backwards ? 512 : limit - 511;
      compare_warm_movs(near_wrap, distant, span / 8, 8, backwards, addr32);
      compare_warm_movs(base, near_wrap, span / 8, 8, backwards, addr32);
    }
  }
}

/* The outer stack alone, checked after every access, with capacities that
 * differ by one, a single-line level, and universes smaller and larger than
 * every capacity. */
static void test_outer_alone(void) {
  const uint32_t capacities[][OUTER_LEVELS] = {{1, 2, 3}, {3, 7, 20}, {5, 6, 40}, {64, 200, 900}};
  const uint32_t universes[] = {2, 5, 30, 80, 5000};
  uint32_t rng = 99991;
  for (unsigned c = 0; c < sizeof(capacities) / sizeof(capacities[0]); ++c) {
    for (unsigned u = 0; u < sizeof(universes) / sizeof(universes[0]); ++u) {
      outer_cache_t *cache = new_outer(capacities[c]);
      struct ref_outer *ref = new_ref_outer(capacities[c]);
      for (unsigned i = 0; i < 6000; ++i) {
        uint32_t r = random_u32(&rng);
        /* Mix random lines with short ascending and descending runs. */
        uintptr_t line = (r >> 8) % universes[u];
        if ((r & 7) == 0) {
          for (unsigned k = 0; k < (r >> 4 & 7); ++k) {
            uintptr_t next = (line + ((r & 8) ? k : -k)) % (universes[u] + 3);
            outer_access(cache, next, false);
            ref_outer_access(ref, next);
          }
        }
        outer_access(cache, line, false);
        ref_outer_access(ref, line);
        verify_outer(cache, ref);
      }
      free(cache->line);
      free(cache);
      free(ref);
    }
  }
}

/* Dependent misses are a subset counted with the same capacities. */
static void test_dependent(void) {
  const uint32_t capacity[OUTER_LEVELS] = {2, 4, 8};
  outer_cache_t *cache = new_outer(capacity);
  uint64_t expected[OUTER_LEVELS] = {0};
  struct ref_outer *ref = new_ref_outer(capacity);
  uint32_t rng = 7;
  for (unsigned i = 0; i < 5000; ++i) {
    uintptr_t line = random_u32(&rng) % 12;
    bool dependent = random_u32(&rng) & 1;
    uint64_t before[OUTER_LEVELS];
    memcpy(before, ref->misses, sizeof(before));
    ref_outer_access(ref, line);
    for (unsigned level = 0; level < OUTER_LEVELS; ++level)
      expected[level] += dependent && ref->misses[level] != before[level];
    outer_access(cache, line, dependent);
  }
  verify_outer(cache, ref);
  for (unsigned level = 0; level < OUTER_LEVELS; ++level)
    assert(cache->dependent_misses[level] == expected[level]);
  free(cache->line);
  free(cache);
  free(ref);
}

int main(void) {
  test_outer_alone();
  test_dependent();
  test_large_stos();
  test_large_movs();
  l1d_cache_t cache = {0};
  struct reference ref = {0};
  attach_outer(&cache, &ref);
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
  free_outer(&cache, &ref);
  puts("L1D and outer reference counts and complete LRU order match");
  return 0;
}

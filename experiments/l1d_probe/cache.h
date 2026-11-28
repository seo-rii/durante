#ifndef DURANTE_EXPERIMENTS_L1D_CACHE_H
#define DURANTE_EXPERIMENTS_L1D_CACHE_H

/* DynamoRIO declares its own C bool and true before including this file. */
#ifndef true
#include <stdbool.h>
#endif
#include <stdint.h>

#include "outer.h"

/* Cold, demand-only, write-allocate 32 KiB L1D using virtual addresses.
 * Tags are MRU first. Adding one to the line number reserves zero for empty. */
enum {
  L1D_LINE_BYTES = 64,
  L1D_SETS = 64,
  L1D_WAYS = 8,
};

typedef struct {
  uint64_t tags[L1D_SETS][L1D_WAYS];
  uint64_t refs, lines, misses;
  /* Optional larger capacities, which see every line that misses this cache in
   * the order of the misses. While dependent is set, the caller's accesses are
   * dependent loads and their misses are also counted separately. */
  outer_cache_t *outer;
  bool dependent;
  uint64_t dependent_misses;
} l1d_cache_t;

static inline void l1d_record_miss(l1d_cache_t *cache, uintptr_t line) {
  ++cache->misses;
  cache->dependent_misses += cache->dependent;
  if (cache->outer)
    outer_access(cache->outer, line, cache->dependent);
}

/* Update replacement order only. Callers account for references and lines. */
static inline void l1d_touch_line(l1d_cache_t *cache, uintptr_t line) {
  uint64_t *set = cache->tags[line % L1D_SETS];
  uint64_t tag = line + 1;
  uint64_t displaced = set[0];
  if (displaced == tag)
    return;

  set[0] = tag;
  for (unsigned way = 1; way < L1D_WAYS; ++way) {
    uint64_t next = set[way];
    set[way] = displaced;
    if (next == tag)
      return;
    displaced = next;
  }
  l1d_record_miss(cache, line);
}

/* A decoded operand is 1..64 bytes. Return whether a second line was used
 * so callers may combine or separately batch their reference/lookup counts. */
static inline bool l1d_touch_operand(l1d_cache_t *cache, uintptr_t addr,
                                     unsigned bytes) {
  uintptr_t first = addr / L1D_LINE_BYTES;
  uintptr_t last = (addr + bytes - 1) / L1D_LINE_BYTES;
  l1d_touch_line(cache, first);
  if (last > first)
    l1d_touch_line(cache, last);
  return last > first;
}

static inline void l1d_access(l1d_cache_t *cache, uintptr_t addr,
                              unsigned bytes) {
  ++cache->refs;
  cache->lines += 1u + l1d_touch_operand(cache, addr, bytes);
}

/* A read immediately followed by a write to exactly the same operand
 * establishes the same LRU order on both passes. */
static inline void l1d_access_twice(l1d_cache_t *cache, uintptr_t addr,
                                    unsigned bytes) {
  cache->refs += 2;
  cache->lines += 2u * (1u + l1d_touch_operand(cache, addr, bytes));
}

/* Number of consecutive whole elements contained in the current line.
 * An unaligned element straddling two lines is handled individually. */
static inline unsigned l1d_line_run(uintptr_t addr, unsigned bytes,
                                    unsigned width_shift, bool backwards) {
  unsigned offset = addr % L1D_LINE_BYTES;
  if (offset + bytes > L1D_LINE_BYTES)
    return 0;
  return backwards ? (offset >> width_shift) + 1
                   : (L1D_LINE_BYTES - offset) >> width_shift;
}

/* Each set sees distinct lines of a contiguous STOS in address order. A
 * straddling element interleaves adjacent lines, which map to different sets.
 * Within a set only the first eight lines can hit preexisting data. */
static inline bool l1d_contiguous_stos(l1d_cache_t *cache, uintptr_t dst,
                                       uint64_t count, unsigned bytes,
                                       unsigned width_shift, bool backwards,
                                       bool addr32) {
  uintptr_t limit = addr32 ? UINT32_MAX : UINTPTR_MAX;
  if (addr32)
    dst = (uint32_t)dst;
  if (bytes - 1 > limit - dst)
    return false;
  uintptr_t available = backwards ? dst : limit - dst - (bytes - 1);
  if (count - 1 > (available >> width_shift))
    return false;
  uintptr_t distance = (count - 1) << width_shift;
  uintptr_t low = backwards ? dst - distance : dst;
  uintptr_t high = (backwards ? dst : dst + distance) + (bytes - 1);
  uintptr_t low_line = low / L1D_LINE_BYTES, high_line = high / L1D_LINE_BYTES;
  uintptr_t line = backwards ? high_line : low_line;
  uintptr_t step = backwards ? -(uintptr_t)1 : 1;
  uintptr_t unique = high_line - low_line + 1;
  const unsigned capacity = L1D_SETS * L1D_WAYS;
  unsigned prefix = unique < capacity ? (unsigned)unique : capacity;
  unsigned i = 0;
  if (backwards && dst / L1D_LINE_BYTES != high_line) {
    /* The first element straddles its two lines and touches the lower one
     * first. They are in different sets, so only the outer caches see the
     * order. */
    l1d_touch_line(cache, high_line - 1);
    l1d_touch_line(cache, high_line);
    line = high_line - 2;
    i = 2;
  }
  for (; i < prefix; ++i) {
    l1d_touch_line(cache, line);
    line += step;
  }
  cache->refs += count;
  cache->lines += count;
  if (dst & (bytes - 1))
    cache->lines += high_line - low_line; /* Each boundary bisects one element. */

  if (unique <= capacity)
    return true;
  /* The rest are misses. Each set retains precisely its last eight lines. */
  cache->misses += unique - capacity;
  if (cache->outer) {
    for (uintptr_t i = capacity; i < unique; ++i) {
      outer_access(cache->outer, line, false);
      line += step;
    }
  }
  line = backwards ? low_line : high_line;
  for (unsigned i = 0; i < L1D_SETS; ++i) {
    uint64_t *set = cache->tags[line % L1D_SETS];
    uintptr_t tag = line + 1;
    for (unsigned way = 0; way < L1D_WAYS; ++way) {
      set[way] = tag;
      tag -= step * L1D_SETS;
    }
    line -= step;
  }
  return true;
}

/* For two disjoint streams moving in lockstep each line pair is visited once.
 * Four complete turns of each stream through the sets replace all eight ways,
 * even when the two streams visit a given set at different times. After that
 * prefix all accesses to new line pairs miss. */
static inline bool l1d_streaming_movs(l1d_cache_t *cache, uintptr_t src,
                                      uintptr_t dst, uint64_t count, unsigned bytes,
                                      unsigned width_shift, bool backwards, bool addr32) {
  _Static_assert(L1D_WAYS % 2 == 0 && (L1D_SETS & (L1D_SETS - 1)) == 0,
                 "paired streams require even associativity and a power-of-two set count");
  const unsigned prefix = L1D_SETS * (L1D_WAYS / 2);
  if (count <= ((uint64_t)prefix * L1D_LINE_BYTES >> width_shift) ||
      (src ^ dst) % L1D_LINE_BYTES != 0 || (src & (bytes - 1)) != 0)
    return false;
  uintptr_t limit = addr32 ? UINT32_MAX : UINTPTR_MAX;
  uintptr_t smaller = src < dst ? src : dst;
  uintptr_t larger = src < dst ? dst : src;
  if (larger > limit - (bytes - 1))
    return false;
  uintptr_t available = backwards ? smaller : limit - (bytes - 1) - larger;
  if (count - 1 > (available >> width_shift))
    return false;

  uintptr_t distance = (uintptr_t)(count - 1) << width_shift;
  uintptr_t source_line = src / L1D_LINE_BYTES;
  uintptr_t dest_line = dst / L1D_LINE_BYTES;
  uintptr_t last = (backwards ? src - distance : src + distance) / L1D_LINE_BYTES;
  uintptr_t pairs = (backwards ? source_line - last : last - source_line) + 1;
  if ((larger - smaller) / L1D_LINE_BYTES < pairs)
    return false;

  uintptr_t step = backwards ? -(uintptr_t)1 : 1;
  uintptr_t read_line = source_line, write_line = dest_line;
  for (unsigned i = 0; i < prefix; ++i) {
    l1d_touch_line(cache, read_line);
    l1d_touch_line(cache, write_line);
    read_line += step;
    write_line += step;
  }
  cache->misses += 2 * (pairs - prefix);
  if (cache->outer) {
    /* Every later pair misses: the outer caches see the read, then the write. */
    for (uintptr_t i = prefix; i < pairs; ++i) {
      outer_access(cache->outer, read_line, false);
      outer_access(cache->outer, write_line, false);
      read_line += step;
      write_line += step;
    }
  }

  /* In the last four turns each set is touched once by each stream per
   * turn. Their relative order repeats. On a tie MOVS writes after reading. */
  uintptr_t last_dest = dest_line + (pairs - 1) * step;
  for (unsigned set = 0; set < L1D_SETS; ++set) {
    unsigned read_age = ((last - set) * step) % L1D_SETS;
    unsigned write_age = ((last_dest - set) * step) % L1D_SETS;
    uint64_t read_tag = last - read_age * step + 1;
    uint64_t write_tag = last_dest - write_age * step + 1;
    bool read_newer = read_age < write_age;
    for (unsigned way = 0; way < L1D_WAYS; way += 2) {
      cache->tags[set][way] = read_newer ? read_tag : write_tag;
      cache->tags[set][way + 1] = read_newer ? write_tag : read_tag;
      read_tag -= step * L1D_SETS;
      write_tag -= step * L1D_SETS;
    }
  }
  /* Naturally aligned elements cannot cross a line. */
  cache->refs += 2 * count;
  cache->lines += 2 * count;
  return true;
}

/* MOVS touches source then destination for each element. While neither line
 * changes, repeating that pair cannot add misses or change its final order,
 * even if both lines map to the same set. STOS needs only its destination. */
static inline void l1d_repeat(l1d_cache_t *cache, bool movs, uintptr_t src,
                              uintptr_t dst, uint64_t count, unsigned bytes,
                              bool backwards, bool addr32) {
  /* String-operation widths are powers of two: 1, 2, 4 or 8. */
  unsigned width_shift = 0;
  while ((1u << width_shift) < bytes)
    ++width_shift;
  if (addr32) {
    src = (uint32_t)src;
    dst = (uint32_t)dst;
    count = (uint32_t)count;
  }
  if (movs && l1d_streaming_movs(cache, src, dst, count, bytes, width_shift, backwards, addr32))
    return;
  if (!movs && count > ((unsigned)L1D_LINE_BYTES >> width_shift) &&
      l1d_contiguous_stos(cache, dst, count, bytes, width_shift, backwards, addr32))
    return;
  while (count) {
    if (addr32) {
      src = (uint32_t)src;
      dst = (uint32_t)dst;
    }
    uint64_t run = l1d_line_run(dst, bytes, width_shift, backwards);
    if (movs) {
      unsigned src_run = l1d_line_run(src, bytes, width_shift, backwards);
      if (run > src_run)
        run = src_run;
    }
    if (run == 0) {
      if (movs)
        l1d_access(cache, src, bytes);
      l1d_access(cache, dst, bytes);
      run = 1;
    } else {
      if (run > count)
        run = count;
      if (movs)
        l1d_touch_line(cache, src / L1D_LINE_BYTES);
      l1d_touch_line(cache, dst / L1D_LINE_BYTES);
      uint64_t accesses = run * (movs ? 2 : 1);
      cache->refs += accesses;
      cache->lines += accesses;
    }
    uintptr_t distance = (uintptr_t)run * bytes;
    uintptr_t step = backwards ? -distance : distance;
    src += step;
    dst += step;
    count -= run;
  }
}

#endif

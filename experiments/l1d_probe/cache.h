#ifndef DURANTE_EXPERIMENTS_L1D_CACHE_H
#define DURANTE_EXPERIMENTS_L1D_CACHE_H

/* DynamoRIO declares its own C bool and true before including this file. */
#ifndef true
#include <stdbool.h>
#endif
#include <stdint.h>

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
} l1d_cache_t;

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
  ++cache->misses;
}

/* A decoded operand is 1..64 bytes, so it touches at most two lines. */
static inline void l1d_access(l1d_cache_t *cache, uintptr_t addr,
                              unsigned bytes) {
  uintptr_t first = addr / L1D_LINE_BYTES;
  uintptr_t last = (addr + bytes - 1) / L1D_LINE_BYTES;
  ++cache->refs;
  ++cache->lines;
  l1d_touch_line(cache, first);
  if (last > first) {
    ++cache->lines;
    l1d_touch_line(cache, last);
  }
}

/* A read immediately followed by a write to exactly the same operand
 * establishes the same LRU order on both passes. */
static inline void l1d_access_twice(l1d_cache_t *cache, uintptr_t addr,
                                    unsigned bytes) {
  l1d_access(cache, addr, bytes);
  ++cache->refs;
  ++cache->lines;
  if ((addr + bytes - 1) / L1D_LINE_BYTES > addr / L1D_LINE_BYTES)
    ++cache->lines;
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
  if (addr32)
    count = (uint32_t)count;
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

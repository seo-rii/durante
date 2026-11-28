// Compare the reduced LRU and FIFO stream history with an independent move-to-front array.
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "outer.h"

int main(void) {
  enum { N = 32 };
  const uint32_t capacity[1] = {N};
  outer_cache_t cache = {0};
  void *storage = calloc(1, outer_storage_bytes(N));
  assert(storage && outer_init(&cache, capacity, storage));
  uint64_t stack[N], history[OUTER_STREAMS], misses = 0, stream = 0, dependent = 0;
  unsigned depth = 0;
  uint32_t seed = 17;
  for (unsigned i = 0; i < 8000; ++i) {
    seed = seed * 1664525u + 1013904223u;
    uint64_t line = i % 7 < 4 ? (i & 127u) : ((seed >> 16) & 63u);
    bool dep = (seed & 0x10) != 0;
    unsigned pos = 0;
    while (pos < depth && stack[pos] != line)
      ++pos;
    if (pos == depth) {
      unsigned live = misses < OUTER_STREAMS ? (unsigned)misses : OUTER_STREAMS;
      for (unsigned h = 0; h < live; ++h) {
        uint64_t d = line > history[h] ? line - history[h] : history[h] - line;
        if (d && d <= OUTER_STREAM_RANGE) {
          ++stream;
          break;
        }
      }
      if (live == OUTER_STREAMS)
        --live;
      memmove(history + 1, history, live * sizeof(*history));
      history[0] = line;
      ++misses;
      dependent += dep;
      if (depth == N)
        --pos;
      else
        ++depth;
    }
    memmove(stack + 1, stack, pos * sizeof(*stack));
    stack[0] = line;
    outer_access(&cache, line, dep);
    assert(cache.misses[0] == misses && cache.stream_misses[0] == stream &&
           cache.dependent_misses[0] == dependent);
  }
  free(storage);
}

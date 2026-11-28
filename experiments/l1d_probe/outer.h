#ifndef DURANTE_EXPERIMENTS_OUTER_CACHE_H
#define DURANTE_EXPERIMENTS_OUTER_CACHE_H

/* DynamoRIO declares its own C bool and true before including this file. */
#ifndef true
#include <stdbool.h>
#endif
#include <stddef.h>
#include <stdint.h>

/* Exact misses of fully associative LRU caches of several capacities, fed by
 * the line addresses that miss the L1D model. One LRU stack holds up to the
 * largest capacity; a node's zone says which capacity first contains it, and
 * one boundary node per capacity marks its last position. An access in zone z
 * misses every capacity below z and moves to the top in O(levels). Because the
 * caches are fully associative, the counts do not depend on how virtual
 * addresses map to sets, only on the sequence of distinct lines.
 *
 * Each miss is also classified as part of a stream when it lands within
 * OUTER_STREAM_RANGE lines of one of the last OUTER_STREAMS misses of the same
 * capacity, as a hardware stream prefetcher would recognize it; the rest are
 * random misses. */
enum {
  OUTER_LEVELS = 3,
  OUTER_STREAMS = 16,
  OUTER_STREAM_RANGE = 2,
};
#define OUTER_NIL UINT32_MAX

typedef struct {
  uint32_t capacity[OUTER_LEVELS]; /* lines, strictly increasing */
  uint32_t head, tail, count;
  uint32_t boundary[OUTER_LEVELS]; /* node at position capacity - 1, or NIL */
  uint64_t *line;                  /* line number of each node */
  uint32_t *prev, *next;
  uint8_t *zone;
  uint32_t *slot; /* open addressing: node + 1, 0 = empty */
  uint32_t mask;
  unsigned shift;
  uint64_t accesses;
  uint64_t misses[OUTER_LEVELS], stream_misses[OUTER_LEVELS];
  /* Misses of accesses the caller marks as dependent loads: loads whose
   * address came from an earlier load, which cannot overlap with it. */
  uint64_t dependent_misses[OUTER_LEVELS];
  uint64_t streams[OUTER_LEVELS][OUTER_STREAMS]; /* line + 1, MRU first; 0 = empty */
} outer_cache_t;

/* Bytes of storage for the arrays of a cache whose largest capacity is lines. */
static inline size_t outer_table_slots(uint32_t lines) {
  size_t slots = 1;
  while (slots < 2 * (size_t)lines)
    slots <<= 1;
  return slots;
}

static inline size_t outer_storage_bytes(uint32_t lines) {
  return (size_t)lines * (sizeof(uint64_t) + 2 * sizeof(uint32_t) + 1) +
         outer_table_slots(lines) * sizeof(uint32_t);
}

/* storage must hold outer_storage_bytes(capacity[OUTER_LEVELS - 1]) zeroed bytes
 * aligned to 8. Capacities must be strictly increasing and nonzero. */
static inline bool outer_init(outer_cache_t *cache, const uint32_t capacity[OUTER_LEVELS],
                              void *storage) {
  for (unsigned i = 0; i < OUTER_LEVELS; ++i) {
    if (!capacity[i] || (i && capacity[i] <= capacity[i - 1]) || capacity[i] >= OUTER_NIL)
      return false;
    cache->capacity[i] = capacity[i];
    cache->boundary[i] = OUTER_NIL;
    cache->misses[i] = cache->stream_misses[i] = cache->dependent_misses[i] = 0;
    for (unsigned s = 0; s < OUTER_STREAMS; ++s)
      cache->streams[i][s] = 0;
  }
  uint32_t lines = capacity[OUTER_LEVELS - 1];
  size_t slots = outer_table_slots(lines);
  char *bytes = storage;
  cache->line = (uint64_t *)bytes;
  bytes += (size_t)lines * sizeof(uint64_t);
  cache->prev = (uint32_t *)bytes;
  bytes += (size_t)lines * sizeof(uint32_t);
  cache->next = (uint32_t *)bytes;
  bytes += (size_t)lines * sizeof(uint32_t);
  cache->slot = (uint32_t *)bytes;
  bytes += slots * sizeof(uint32_t);
  cache->zone = (uint8_t *)bytes;
  cache->mask = (uint32_t)(slots - 1);
  cache->shift = 64;
  for (size_t s = slots; s > 1; s >>= 1)
    --cache->shift;
  cache->head = cache->tail = OUTER_NIL;
  cache->count = 0;
  cache->accesses = 0;
  return true;
}

static inline uint32_t outer_hash(const outer_cache_t *cache, uint64_t line) {
  return (uint32_t)((line * 0x9E3779B97F4A7C15ull) >> cache->shift) & cache->mask;
}

static inline uint32_t outer_find(const outer_cache_t *cache, uint64_t line) {
  for (uint32_t i = outer_hash(cache, line);; i = (i + 1) & cache->mask) {
    uint32_t entry = cache->slot[i];
    if (!entry)
      return OUTER_NIL;
    if (cache->line[entry - 1] == line)
      return entry - 1;
  }
}

static inline void outer_insert_slot(outer_cache_t *cache, uint32_t node) {
  uint32_t i = outer_hash(cache, cache->line[node]);
  while (cache->slot[i])
    i = (i + 1) & cache->mask;
  cache->slot[i] = node + 1;
}

/* Linear-probing deletion by backward shift keeps every remaining key on the
 * probe path from its home slot. */
static inline void outer_remove_slot(outer_cache_t *cache, uint32_t node) {
  uint32_t i = outer_hash(cache, cache->line[node]);
  while (cache->slot[i] != node + 1)
    i = (i + 1) & cache->mask;
  for (uint32_t j = (i + 1) & cache->mask; cache->slot[j]; j = (j + 1) & cache->mask) {
    uint32_t home = outer_hash(cache, cache->line[cache->slot[j] - 1]);
    /* Move j into the hole at i unless its home lies cyclically in (i, j]. */
    bool stays = i <= j ? (i < home && home <= j) : (i < home || home <= j);
    if (!stays) {
      cache->slot[i] = cache->slot[j];
      i = j;
    }
  }
  cache->slot[i] = 0;
}

static inline void outer_unlink(outer_cache_t *cache, uint32_t node) {
  uint32_t p = cache->prev[node], n = cache->next[node];
  if (p == OUTER_NIL)
    cache->head = n;
  else
    cache->next[p] = n;
  if (n == OUTER_NIL)
    cache->tail = p;
  else
    cache->prev[n] = p;
}

static inline void outer_push_head(outer_cache_t *cache, uint32_t node) {
  cache->prev[node] = OUTER_NIL;
  cache->next[node] = cache->head;
  if (cache->head != OUTER_NIL)
    cache->prev[cache->head] = node;
  cache->head = node;
  if (cache->tail == OUTER_NIL)
    cache->tail = node;
  cache->zone[node] = 0;
}

static inline void outer_miss(outer_cache_t *cache, unsigned level, uint64_t line,
                              bool dependent) {
  uint64_t *recent = cache->streams[level];
  unsigned found = OUTER_STREAMS - 1;
  bool stream = false;
  for (unsigned s = 0; s < OUTER_STREAMS && recent[s]; ++s) {
    uint64_t last = recent[s] - 1;
    uint64_t distance = line > last ? line - last : last - line;
    if (distance && distance <= OUTER_STREAM_RANGE) {
      found = s;
      stream = true;
      break;
    }
  }
  ++cache->misses[level];
  cache->stream_misses[level] += stream;
  cache->dependent_misses[level] += dependent;
  for (unsigned s = found; s > 0; --s)
    recent[s] = recent[s - 1];
  recent[0] = line + 1;
}

static inline void outer_access(outer_cache_t *cache, uint64_t line, bool dependent) {
  ++cache->accesses;
  uint32_t node = outer_find(cache, line);
  if (node != OUTER_NIL && node == cache->head)
    return;
  unsigned zone = node == OUTER_NIL ? OUTER_LEVELS : cache->zone[node];
  for (unsigned level = 0; level < zone; ++level)
    outer_miss(cache, level, line, dependent);

  if (node != OUTER_NIL) {
    uint32_t before = cache->prev[node];
    /* Every capacity below the node's zone gains the node at its top, so the
     * last element of each moves to the next zone. The element in front of
     * that one becomes the last, or the node itself when there is none. */
    for (unsigned level = 0; level < zone; ++level) {
      uint32_t last = cache->boundary[level];
      cache->zone[last] = (uint8_t)(level + 1);
      cache->boundary[level] = cache->prev[last] == OUTER_NIL ? node : cache->prev[last];
    }
    /* Positions behind the node keep their places; the node's own position is
     * taken by the element in front of it. */
    if (cache->boundary[zone] == node)
      cache->boundary[zone] = before;
    outer_unlink(cache, node);
    outer_push_head(cache, node);
    return;
  }

  uint32_t largest = cache->capacity[OUTER_LEVELS - 1];
  bool to_new[OUTER_LEVELS] = {false};
  for (unsigned level = 0; level < OUTER_LEVELS; ++level) {
    uint32_t last = cache->boundary[level];
    if (last == OUTER_NIL)
      continue;
    cache->zone[last] = (uint8_t)(level + 1);
    cache->boundary[level] = cache->prev[last];
    /* The new node, inserted below, will be the only element in front. */
    to_new[level] = cache->prev[last] == OUTER_NIL;
  }
  if (cache->count == largest) {
    /* The old last element of the largest capacity has left every cache. */
    node = cache->tail;
    outer_remove_slot(cache, node);
    outer_unlink(cache, node);
  } else {
    node = cache->count++;
  }
  cache->line[node] = line;
  outer_insert_slot(cache, node);
  outer_push_head(cache, node);
  for (unsigned level = 0; level < OUTER_LEVELS; ++level) {
    if (to_new[level])
      cache->boundary[level] = node;
    else if (cache->boundary[level] == OUTER_NIL && cache->count == cache->capacity[level])
      cache->boundary[level] = cache->tail;
  }
}

#endif

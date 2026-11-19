#ifndef DURANTE_STORE_LOAD_RANGE_H
#define DURANTE_STORE_LOAD_RANGE_H
#include <stdint.h>

typedef enum { DISJOINT, COVERED, PARTIAL, INVALID_RANGE } range_relation_t;

/* All intervals include both endpoints. Real memory operands have nonzero
 * width. A range wrapping around the address space cannot qualify. */
static inline range_relation_t range_relation(uintptr_t store, unsigned stored,
                                               uintptr_t load, unsigned loaded) {
  if (!stored || !loaded || stored - 1 > UINTPTR_MAX - store ||
      loaded - 1 > UINTPTR_MAX - load)
    return INVALID_RANGE;
  uintptr_t store_last = store + (stored - 1);
  uintptr_t load_last = load + (loaded - 1);
  if (store_last < load || load_last < store)
    return DISJOINT;
  return store <= load && load_last <= store_last ? COVERED : PARTIAL;
}
#endif

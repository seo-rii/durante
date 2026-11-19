#include "range.h"
#include <stdio.h>

int main(void) {
  const struct {
    uintptr_t store, load;
    unsigned stored, loaded;
    range_relation_t expected;
  } cases[] = {
      {16, 16, 8, 8, COVERED}, {16, 18, 8, 4, COVERED},
      {16, 17, 8, 8, PARTIAL}, {16, 15, 8, 8, PARTIAL},
      {16, 16, 1, 8, PARTIAL}, {16, 8, 8, 8, DISJOINT},
      {16, 24, 8, 8, DISJOINT}, {0, UINTPTR_MAX, 1, 1, DISJOINT},
      {UINTPTR_MAX - 7, UINTPTR_MAX, 8, 1, COVERED},
      {0, UINTPTR_MAX - 1, 8, 4, INVALID_RANGE},
      {UINTPTR_MAX, 0, 2, 1, INVALID_RANGE},
      {0, 0, 0, 1, INVALID_RANGE}, {0, 0, 1, 0, INVALID_RANGE},
  };
  for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
    if (range_relation(cases[i].store, cases[i].stored, cases[i].load,
                       cases[i].loaded) != cases[i].expected) {
      fprintf(stderr, "range case %u failed\n", i);
      return 1;
    }
  }
  return 0;
}

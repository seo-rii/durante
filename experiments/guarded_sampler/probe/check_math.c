#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include "guard_math.h"

int main(void) {
    uint64_t n = 0;
    assert(!add_weighted(&n, UINT64_MAX / 64 + 1, 64) && n == 0);
    assert(add_weighted(&n, UINT64_MAX / 64, 64) && n == UINT64_MAX - 63);
    assert(!add_weighted(&n, 1, 64) && n == UINT64_MAX - 63);
    assert(add_weighted(&n, 63, 1) && n == UINT64_MAX);
    assert(add_weighted(&n, 0, 64) && n == UINT64_MAX);
    assert(!add_weighted(&n, 1, 1) && n == UINT64_MAX);
    return 0;
}

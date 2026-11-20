#include <cinttypes>
#include <cstdio>
#include <vector>

static uint32_t advance(uint32_t &state) {
    state ^= state << 13;
    state ^= state >> 17;
    state ^= state << 5;
    return state;
}

int main() {
    uint32_t size, phases, operations, state;
    if (std::scanf("%" SCNu32 " %" SCNu32 " %" SCNu32 " %" SCNu32,
                   &size, &phases, &operations, &state) != 4 ||
        size == 0 || size >= (1u << 31) || (size & (size - 1)) != 0)
        return 1;

    std::vector<uint64_t> tree(size + 1);
    uint64_t total = 0;
    for (uint32_t phase = 0; phase < phases; ++phase) {
        for (uint32_t i = 0; i < operations; ++i) {
            const uint32_t word = advance(state);
            const uint32_t value = 1 + (word >> 27);
            for (uint32_t pos = 1 + (word & (size - 1)); pos <= size; pos += pos & -pos)
                tree[pos] += value;
        }
        for (uint32_t i = 0; i < operations; ++i) {
            for (uint32_t pos = 1 + (advance(state) & (size - 1)); pos; pos &= pos - 1)
                total += tree[pos];
        }
    }
    std::printf("%" PRIu64 "\n", total);
}

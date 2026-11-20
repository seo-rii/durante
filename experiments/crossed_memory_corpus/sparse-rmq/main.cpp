#include <algorithm>
#include <bit>
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
    uint32_t size, queries, state;
    if (std::scanf("%" SCNu32 " %" SCNu32 " %" SCNu32, &size, &queries, &state) != 3 ||
        size == 0 || size >= (1u << 30) || (size & (size - 1)) != 0)
        return 1;
    std::vector<std::vector<uint32_t>> table(std::bit_width(size));
    table[0].resize(size);
    for (auto &value : table[0])
        value = advance(state);
    for (unsigned k = 1; k < table.size(); ++k) {
        const uint32_t span = 1u << k;
        table[k].resize(size - span + 1);
        for (uint32_t i = 0; i <= size - span; ++i)
            table[k][i] = std::min(table[k-1][i], table[k-1][i + span / 2]);
    }
    uint64_t total = 0;
    for (uint32_t q = 0; q < queries; ++q) {
        const uint32_t left = advance(state) & (size - 1);
        const uint32_t length = std::min(1 + (advance(state) >> 26), size - left);
        const unsigned k = std::bit_width(length) - 1;
        total += std::min(table[k][left], table[k][left + length - (1u << k)]);
    }
    std::printf("%" PRIu64 "\n", total);
}

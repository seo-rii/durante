#include <cinttypes>
#include <cstdint>
#include <cstdio>
#include <vector>

uint32_t position(uint32_t rank, uint32_t mask) {
    uint32_t out = (rank + 173531u) & mask;
    out = ((out ^ (out >> 7)) * 107711u) & mask;
    out = ((out ^ (out >> 9)) * 69353u) & mask;
    return out ^ (out >> 5);
}

int main() {
    uint32_t nodes, queries, state;
    if (std::scanf("%" SCNu32 " %" SCNu32 " %" SCNu32, &nodes, &queries, &state) != 3 ||
        nodes == 0 || (nodes & (nodes - 1))) return 1;
    const uint32_t mask = nodes - 1;
    std::vector<uint32_t> successor(nodes);
    for (uint32_t i = 0; i < nodes; ++i) successor[position(i, mask)] = position(i + 1, mask);
    uint64_t total = 0;
    for (uint32_t q = 0; q < queries; ++q) {
        state = state * 1103515245u + 12345u;
        uint32_t at = position(state & mask, mask);
        const uint32_t hops = 1024 + (q % 512);
        for (uint32_t k = 0; k < hops; ++k) at = successor[at];
        total += at;
    }
    std::printf("%" PRIu64 "\n", total);
}

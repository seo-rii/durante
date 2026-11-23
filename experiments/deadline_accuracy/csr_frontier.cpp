#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <numeric>
#include <vector>

// BFS shortest-path sums in a directed graph, stored in compressed sparse rows.
int main() {
    unsigned bits, traversals, relabel;
    if (std::scanf("%u%u%u", &bits, &traversals, &relabel) != 3 || bits < 4 ||
        bits > 20 || traversals == 0 || traversals > 32 || relabel > 1)
        return 1;
    unsigned count = 1u << bits, mask = count - 1;
    std::vector<unsigned> label(count), offsets(count + 1), distance(count), queue(count);
    std::iota(label.begin(), label.end(), 0u);
    if (relabel) {
        std::uint32_t state = 0x6a09e667u;
        for (unsigned i = count - 1; i > 0; --i) {
            state = state * 1664525u + 1013904223u;
            std::swap(label[i], label[(static_cast<std::uint64_t>(state) * (i + 1)) >> 32]);
        }
    }
    for (unsigned i = 0; i < count; ++i)
        offsets[label[i] + 1] = 2 + (i & 1);
    std::partial_sum(offsets.begin(), offsets.end(), offsets.begin());
    std::vector<unsigned> edges(offsets.back());
    for (unsigned i = 0; i < count; ++i) {
        unsigned start = offsets[label[i]];
        edges[start] = label[(i + 1) & mask];
        edges[start + 1] = label[(i + 17 + (i & 63)) & mask];
        if (i & 1)
            edges[start + 2] = label[(i + 251) & mask];
    }
    std::uint64_t total = 0;
    for (unsigned t = 0; t < traversals; ++t) {
        std::fill(distance.begin(), distance.end(), count);
        unsigned root = label[(t * 104729u) & mask];
        distance[root] = 0;
        queue[0] = root;
        unsigned tail = 1;
        for (unsigned head = 0; head < tail; ++head) {
            unsigned u = queue[head];
            for (unsigned edge = offsets[u]; edge < offsets[u + 1]; ++edge) {
                unsigned v = edges[edge];
                if (distance[v] == count) {
                    distance[v] = distance[u] + 1;
                    queue[tail++] = v;
                    total += distance[v];
                }
            }
        }
    }
    std::printf("%llu\n", static_cast<unsigned long long>(total));
}

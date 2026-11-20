#include <cinttypes>
#include <cstdio>
#include <numeric>
#include <vector>

static uint32_t advance(uint32_t &state) {
    state ^= state << 13;
    state ^= state >> 17;
    state ^= state << 5;
    return state;
}

static uint32_t root(std::vector<uint32_t> &parent, uint32_t node) {
    while (parent[node] != node) {
        parent[node] = parent[parent[node]];
        node = parent[node];
    }
    return node;
}

int main() {
    uint32_t nodes, graphs, edges, queries, state;
    if (std::scanf("%" SCNu32 " %" SCNu32 " %" SCNu32 " %" SCNu32 " %" SCNu32,
                   &nodes, &graphs, &edges, &queries, &state) != 5 ||
        nodes == 0 || (nodes & (nodes - 1)) != 0)
        return 1;
    uint64_t connected = 0, ordinal_sum = 0;
    for (uint32_t graph = 0; graph < graphs; ++graph) {
        std::vector<uint32_t> parent(nodes), sizes(nodes, 1);
        std::iota(parent.begin(), parent.end(), 0u);
        for (uint32_t edge = 0; edge < edges; ++edge) {
            uint32_t a = root(parent, advance(state) & (nodes - 1));
            uint32_t b = root(parent, advance(state) & (nodes - 1));
            if (a != b) {
                if (sizes[a] < sizes[b])
                    std::swap(a, b);
                parent[b] = a;
                sizes[a] += sizes[b];
            }
        }
        for (uint32_t i = 0; i < queries; ++i) {
            const uint32_t a = root(parent, advance(state) & (nodes - 1));
            const uint32_t b = root(parent, advance(state) & (nodes - 1));
            if (a == b) {
                ++connected;
                ordinal_sum += static_cast<uint64_t>(graph) * queries + i + 1;
            }
        }
    }
    std::printf("%" PRIu64 " %" PRIu64 "\n", connected, ordinal_sum);
}

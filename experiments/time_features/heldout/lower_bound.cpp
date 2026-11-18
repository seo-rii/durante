#include <algorithm>
#include <array>
#include <cinttypes>
#include <cstdint>
#include <cstdio>

int main() {
    std::uint32_t queries;
    if (std::scanf("%" SCNu32, &queries) != 1 || queries == 0 || queries > 786432) {
        return 2;
    }

    constexpr std::uint32_t size = 1u << 18;
    static std::array<std::uint32_t, size> table;
    // Each interval [4i, 4i+3] contains exactly one table entry.
    for (std::uint32_t i = 0; i < size; ++i) {
        const auto offset = ((i * 2654435761u >> 16) ^ i) & 3u;
        table[i] = 4 * i + offset;
    }
    std::uint64_t checksum = 0;
    for (std::uint32_t i = 0; i < queries; ++i) {
        const auto bucket = (i * 40503u + 44257u) & (size - 1);
        const auto key = 4 * bucket + ((i >> 5) & 3u);
        const auto found = std::lower_bound(table.begin(), table.end(), key);
        checksum += (std::uint64_t(i) + 1) * (found - table.begin());
    }
    std::printf("%" PRIu64 "\n", checksum);
}

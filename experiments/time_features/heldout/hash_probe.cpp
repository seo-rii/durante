#include <array>
#include <cinttypes>
#include <cstdint>
#include <cstdio>

constexpr std::uint32_t size = 65536;
constexpr std::uint32_t mask = size - 1;
struct Entry {
    std::uint32_t key_plus_one, value;
};

static std::uint32_t first_slot(std::uint32_t key) {
    key *= 0x7feb352du;
    key ^= key >> 15;
    key *= 0x846ca68bu;
    return (key ^ (key >> 16)) & mask;
}

int main() {
    std::uint32_t queries;
    if (std::scanf("%" SCNu32, &queries) != 1 || queries == 0 || queries > 4194304) {
        return 2;
    }

    static std::array<Entry, size> table;
    for (std::uint32_t i = 0; i < 40000; ++i) {
        const auto key = (i * 40503u + 44257u) & mask;
        auto slot = first_slot(key);
        while (table[slot].key_plus_one != 0) {
            slot = (slot + 1) & mask;
        }
        table[slot] = {key + 1, 1 + (key ^ (i * 97u))};
    }

    std::uint64_t checksum = 0;
    for (std::uint32_t i = 0; i < queries; ++i) {
        const auto key = (i * 25173u + 13849u) & mask;
        auto slot = first_slot(key);
        while (table[slot].key_plus_one != 0) {
            if (table[slot].key_plus_one == key + 1) {
                checksum += (std::uint64_t(i) + 1) * table[slot].value;
                break;
            }
            slot = (slot + 1) & mask;
        }
    }
    std::printf("%" PRIu64 "\n", checksum);
}

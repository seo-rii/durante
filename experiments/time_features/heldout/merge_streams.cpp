#include <array>
#include <cinttypes>
#include <cstdint>
#include <cstdio>

static std::uint32_t jitter(std::uint32_t x) {
    x ^= x >> 16;
    x *= 0x85ebca6bu;
    x ^= x >> 13;
    x *= 0xc2b2ae35u;
    return (x ^ (x >> 16)) & 7u;
}

int main() {
    std::uint32_t passes;
    if (std::scanf("%" SCNu32, &passes) != 1 || passes == 0 || passes > 64) {
        return 2;
    }

    constexpr std::uint32_t size = 65536;
    static std::array<std::uint32_t, size> left, right;
    for (std::uint32_t i = 0; i < size; ++i) {
        left[i] = 8 * i + jitter(i ^ 0x13579bdfu);
        right[i] = 8 * i + jitter(i ^ 0x2468ace0u);
    }

    std::uint64_t checksum = 0, rank = 0;
    for (std::uint32_t pass = 0; pass < passes; ++pass) {
        std::uint32_t i = 0, j = 0;
        while (i < size || j < size) {
            // The stream tag in the checksum distinguishes equal keys.
            std::uint32_t value;
            if (j == size || (i < size && left[i] <= right[j])) {
                value = 2 * left[i++] + 1;
            } else {
                value = 2 * right[j++] + 2;
            }
            checksum += ++rank * value;
        }
    }
    std::printf("%" PRIu64 "\n", checksum);
}

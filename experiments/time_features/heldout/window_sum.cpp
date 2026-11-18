#include <array>
#include <cinttypes>
#include <cstdint>
#include <cstdio>

int main() {
    std::uint32_t steps;
    if (std::scanf("%" SCNu32, &steps) != 1 || steps == 0 || steps > 67108864) {
        return 2;
    }

    constexpr std::uint32_t size = 1u << 16;
    constexpr std::uint32_t width = 251;
    static std::array<std::uint32_t, size> values;
    for (std::uint32_t i = 0; i < size; ++i) {
        values[i] = (i * 25173u + 13849u) & (size - 1);
    }
    // Begin with the window ending at the last element of the ring.
    std::uint32_t window = 0;
    for (std::uint32_t i = size - width; i < size; ++i) {
        window += values[i];
    }
    std::uint64_t checksum = 0;
    for (std::uint32_t i = 0; i < steps; ++i) {
        window -= values[(i - width) & (size - 1)];
        window += values[i & (size - 1)];
        checksum += (std::uint64_t(i) + 1) * window;
    }
    std::printf("%" PRIu64 "\n", checksum);
}

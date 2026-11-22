#include <cinttypes>
#include <cstdint>
#include <cstdio>

alignas(64) static volatile std::uint32_t table[256];

extern "C" __attribute__((noinline)) std::uint64_t forward(std::uint32_t steps) {
    std::uint64_t sum = 0;
    for (std::uint32_t i = 0; i < steps; ++i)
        sum += table[i & 255U];
    return sum;
}

extern "C" __attribute__((noinline)) std::uint64_t reverse(std::uint32_t steps) {
    std::uint64_t sum = 0;
    for (std::uint32_t i = 0; i < steps; ++i)
        sum += table[255U - (i & 255U)] ^ 0x5aU;
    return sum;
}

int main() {
    char mode;
    std::uint32_t steps;
    if (std::scanf(" %c %" SCNu32, &mode, &steps) != 2 ||
        (mode != 'A' && mode != 'B') || steps == 0)
        return 2;
    for (std::uint32_t i = 0; i < 256; ++i)
        table[i] = 17U * i + 3U;
    const auto total = mode == 'A' ? forward(steps) : reverse(steps);
    std::printf("%" PRIu64 "\n", total);
}

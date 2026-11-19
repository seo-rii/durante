#include <cinttypes>
#include <cstdint>
#include <cstdio>
#include <vector>

int main() {
    uint32_t size, points;
    if (std::scanf("%" SCNu32 " %" SCNu32, &size, &points) != 2) return 1;
    constexpr uint64_t prime = 1000000007;
    std::vector<uint32_t> coefficients(size);
    for (uint32_t i = 0; i < size; ++i) coefficients[i] = 17u * i + 22u;
    uint64_t total = 0;
    for (uint32_t j = 0; j < points; ++j) {
        const uint32_t x = 3u + 37u * j;
        uint64_t value = 0;
        for (const auto coefficient : coefficients) value = (value * x + coefficient) % prime;
        total += value;
    }
    std::printf("%" PRIu64 "\n", total);
}

#include <algorithm>
#include <bit>
#include <cinttypes>
#include <cstdint>
#include <cstdio>

uint32_t binary_gcd(uint32_t a, uint32_t b) {
    if (!a || !b) return a | b;
    const unsigned factor = std::countr_zero(a | b);
    a >>= std::countr_zero(a);
    do {
        b >>= std::countr_zero(b);
        if (a > b) std::swap(a, b);
        b -= a;
    } while (b);
    return a << factor;
}

int main() {
    uint32_t queries, state;
    if (std::scanf("%" SCNu32 " %" SCNu32, &queries, &state) != 2) return 1;
    const auto next = [&] { return state = 1664525u * state + 1013904223u; };
    uint64_t total = 0;
    for (uint32_t i = 0; i < queries; ++i) {
        const uint32_t a = next();
        const uint32_t b = next();
        total += binary_gcd(a, b);
    }
    std::printf("%" PRIu64 "\n", total);
}

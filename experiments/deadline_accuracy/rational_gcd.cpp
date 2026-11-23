#include <cstdint>
#include <cstdio>

// Sum the common factors needed to reduce a sequence of fractions.
static std::uint64_t draw(std::uint64_t &state) {
    state ^= state << 13;
    state ^= state >> 7;
    state ^= state << 17;
    return state;
}

int main() {
    unsigned pairs, distribution;
    if (std::scanf("%u%u", &pairs, &distribution) != 2 || pairs == 0 || pairs > 4000000 ||
        distribution > 2)
        return 1;
    std::uint64_t state = 0x589146c7ab810e69ull, sum = 0;
    for (unsigned i = 0; i < pairs; ++i) {
        std::uint64_t a = draw(state);
        std::uint64_t b = draw(state) | 1u;
        if (distribution == 0) {
            b &= 255u;
        } else if (distribution == 2) {
            // Consecutive Fibonacci numbers force many successive quotients of one.
            std::uint64_t scale = (a & ((1u << 20) - 1)) + 1;
            a = 267914296ull * scale;
            b = 165580141ull * scale;
        }
        while (b != 0) {
            std::uint64_t remainder = a % b;
            a = b;
            b = remainder;
        }
        sum += a;
    }
    std::printf("%llu\n", static_cast<unsigned long long>(sum));
}

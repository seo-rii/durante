#include <cstdint>
#include <cstdio>
#include <vector>

// Walk a single successor cycle whose physical layout is permuted within each power-of-two block.
static std::uint32_t label(std::uint32_t logical, std::uint32_t mask) {
    std::uint32_t low = logical & mask;
    low = ((low ^ (low >> 11)) * 0x9e3779b1u) & mask;
    low ^= low >> 7;
    return (logical & ~mask) | low;
}

int main() {
    unsigned size_bits, block_bits, steps;
    if (std::scanf("%u%u%u", &size_bits, &block_bits, &steps) != 3 || size_bits < 4 ||
        size_bits > 24 || block_bits > size_bits || steps == 0 || steps > 40000000)
        return 1;
    unsigned size = 1u << size_bits;
    unsigned block_mask = (1u << block_bits) - 1;
    std::vector<std::uint32_t> next(size);
    unsigned at = 0;
    for (unsigned logical = 1; logical < size; ++logical) {
        unsigned to = label(logical, block_mask);
        next[at] = to;
        at = to;
    }
    next[at] = 0;
    std::uint64_t total = 0;
    at = 0;
    for (unsigned i = 0; i < steps; ++i) {
        at = next[at];
        total += at;
    }
    std::printf("%u %llu\n", at, static_cast<unsigned long long>(total));
}

#include <cstdint>
#include <iostream>
#include <vector>

static std::vector<uint32_t> sequence(uint32_t count, uint32_t state) {
    std::vector<uint32_t> values;
    values.reserve(count);
    uint32_t current = 0;
    for (uint32_t i = 0; i < count; ++i) {
        state ^= state << 13;
        state ^= state >> 17;
        state ^= state << 5;
        current += 1 + (state & 7);
        values.push_back(current);
    }
    return values;
}

int main() {
    uint32_t count, seed;
    if (!(std::cin >> count >> seed) || count > (1u << 20))
        return 2;
    const auto a = sequence(count, seed);
    const auto b = sequence(count, seed ^ 0x326ff937u);
    uint64_t sum = 0;
    std::size_t i = 0, j = 0;
    while (i < a.size() && j < b.size()) {
        const auto left = a[i], right = b[j];
        if (left == right)
            sum += left;
        i += left <= right;
        j += right <= left;
    }
    std::cout << sum << '\n';
}

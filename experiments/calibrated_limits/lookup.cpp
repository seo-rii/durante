#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <vector>

int main() {
    unsigned size, queries, shuffled;
    if (std::scanf("%u%u%u", &size, &queries, &shuffled) != 3 || size == 0 || size > (1U << 22))
        return 1;
    std::vector<unsigned> keys(size);
    for (unsigned i = 0; i < size; ++i)
        keys[i] = 2 * i;
    std::uint32_t state = 12345;
    std::uint64_t sum = 0;
    for (unsigned i = 0; i < queries; ++i) {
        state = state * 1664525U + 1013904223U;
        unsigned query = (shuffled ? state >> 5 : i) % (2 * size);
        sum += std::lower_bound(keys.begin(), keys.end(), query) - keys.begin();
    }
    std::printf("%llu\n", static_cast<unsigned long long>(sum));
}

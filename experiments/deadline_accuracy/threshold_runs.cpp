#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <vector>

// Count the longest qualifying run and all qualifying subarrays in a cyclic event stream.
int main() {
    unsigned length, passes, pattern;
    if (std::scanf("%u%u%u", &length, &passes, &pattern) != 3 || length == 0 ||
        length > (1u << 22) || passes == 0 || passes > 200 || pattern > 2)
        return 1;
    std::vector<std::uint8_t> events(length);
    std::uint32_t random = 0x31415926u;
    for (unsigned i = 0; i < length; ++i) {
        random ^= random << 13;
        random ^= random >> 17;
        random ^= random << 5;
        // Approximately half of all events qualify in each stream.
        events[i] = pattern == 0 ? ((i / 64) & 1) : pattern == 1 ? (random >> 31) : ((i / 5) & 1);
    }
    std::uint64_t subarrays = 0;
    unsigned run = 0, longest = 0;
    for (unsigned pass = 0; pass < passes; ++pass)
        for (auto event : events) {
            if (event) {
                subarrays += ++run;
            } else {
                longest = std::max(longest, run);
                run = 0;
            }
        }
    std::printf("%u %llu\n", std::max(longest, run), static_cast<unsigned long long>(subarrays));
}

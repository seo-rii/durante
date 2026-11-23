#include <cstdint>
#include <cstdio>
#include <vector>

int main() {
    unsigned categories, events;
    constexpr unsigned window = 8192;
    if (std::scanf("%u%u", &categories, &events) != 2 || !categories ||
        categories > (1U << 22) || (categories & (categories - 1)))
        return 1;

    std::vector<unsigned> frequency(categories);
    std::vector<unsigned> history(window);
    std::uint32_t state = 12345;
    unsigned distinct = 0;
    std::uint64_t total = 0;
    for (unsigned step = 0; step < events; ++step) {
        state = state * 1664525U + 1013904223U;
        unsigned incoming = (state >> 8) & (categories - 1);
        unsigned slot = step & (window - 1);
        if (step >= window && --frequency[history[slot]] == 0)
            --distinct;
        history[slot] = incoming;
        if (++frequency[incoming] == 1)
            ++distinct;
        total += distinct;
    }
    std::printf("%llu\n", static_cast<unsigned long long>(total));
}

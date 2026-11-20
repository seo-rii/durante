#include <algorithm>
#include <cinttypes>
#include <cstdio>
#include <vector>

struct Bar {
    uint32_t height, start;
};

int main() {
    uint32_t width, rounds, state;
    if (std::scanf("%" SCNu32 " %" SCNu32 " %" SCNu32, &width, &rounds, &state) != 3 ||
        width == UINT32_MAX)
        return 1;
    std::vector<Bar> stack;
    stack.reserve(width);
    uint64_t total = 0;
    for (uint32_t round = 0; round < rounds; ++round) {
        uint64_t best = 0;
        for (uint32_t i = 0; i <= width; ++i) {
            uint32_t height = 0, start = i;
            if (i < width) {
                state ^= state << 13;
                state ^= state >> 17;
                state ^= state << 5;
                height = 1 + (state >> 16);
            }
            while (!stack.empty() && stack.back().height >= height) {
                const auto bar = stack.back();
                stack.pop_back();
                best = std::max(best, uint64_t(bar.height) * (i - bar.start));
                start = bar.start;
            }
            if (height)
                stack.push_back({height, start});
        }
        total += best;
    }
    std::printf("%" PRIu64 "\n", total);
}

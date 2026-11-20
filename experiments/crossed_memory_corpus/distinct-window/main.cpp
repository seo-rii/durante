#include <algorithm>
#include <cinttypes>
#include <cstdio>
#include <vector>

int main() {
    uint32_t count, alphabet, state;
    if (std::scanf("%" SCNu32 " %" SCNu32 " %" SCNu32, &count, &alphabet, &state) != 3 ||
        alphabet == 0 || (alphabet & (alphabet - 1)) != 0 || count == UINT32_MAX)
        return 1;
    std::vector<uint32_t> last(alphabet);
    uint32_t left = 0;
    uint64_t total = 0;
    for (uint32_t i = 0; i < count; ++i) {
        state ^= state << 13;
        state ^= state >> 17;
        state ^= state << 5;
        const uint32_t symbol = state & (alphabet - 1);
        left = std::max(left, last[symbol]);
        total += i - left + 1;
        last[symbol] = i + 1;
    }
    std::printf("%" PRIu64 "\n", total);
}

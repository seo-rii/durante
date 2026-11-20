#include <array>
#include <cinttypes>
#include <cstdio>
#include <cstring>
#include <string_view>
#include <vector>

int main() {
    uint32_t blocks, state;
    if (std::scanf("%" SCNu32 " %" SCNu32, &blocks, &state) != 2)
        return 1;
    std::vector<char> text(static_cast<size_t>(blocks) * 9);
    constexpr std::string_view tails = "aabc";
    for (size_t begin = 0; begin < text.size(); begin += 9) {
        std::memcpy(text.data() + begin, "abababab", 8);
        state ^= state << 13;
        state ^= state >> 17;
        state ^= state << 5;
        text[begin + 8] = tails[state >> 30];
    }

    constexpr std::string_view pattern = "ababababc";
    std::array<size_t, pattern.size()> prefix{};
    for (size_t i = 1, matched = 0; i < pattern.size(); ++i) {
        while (matched && pattern[i] != pattern[matched])
            matched = prefix[matched - 1];
        if (pattern[i] == pattern[matched])
            ++matched;
        prefix[i] = matched;
    }
    uint64_t hits = 0, end_sum = 0;
    for (size_t i = 0, matched = 0; i < text.size(); ++i) {
        while (matched && text[i] != pattern[matched])
            matched = prefix[matched - 1];
        if (text[i] == pattern[matched])
            ++matched;
        if (matched == pattern.size()) {
            ++hits;
            end_sum += i + 1;
            matched = prefix[matched - 1];
        }
    }
    std::printf("%" PRIu64 " %" PRIu64 "\n", hits, end_sum);
}

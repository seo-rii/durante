// Scan the logical pages and adjacent query distances of append/print Scrivener inputs.
#include <algorithm>
#include <array>
#include <bit>
#include <cctype>
#include <climits>
#include <cstdio>

static int next_token() {
    int ch;
    do {
        ch = getchar_unlocked();
    } while (std::isspace(ch));
    return ch;
}

static bool end_of_token(int ch) {
    return ch == EOF || std::isspace(ch);
}

static bool number(int& value) {
    int ch = next_token();
    if (ch < '0' || ch > '9') return false;
    value = 0;
    do {
        int digit = ch - '0';
        if (value > (INT_MAX - digit) / 10) return false;
        value = 10 * value + digit;
        ch = getchar_unlocked();
    } while (ch >= '0' && ch <= '9');
    return end_of_token(ch);
}

int main(int argc, char**) {
    int operations;
    if (argc != 1 || !number(operations)) return 1;
    std::array<int, 16> recent;
    recent.fill(-1);
    int length = 0, previous = -1, accesses = 0, misses = 0;
    long long position_bits = 0;
    for (int i = 0; i < operations; ++i) {
        int command = next_token();
        if (!end_of_token(getchar_unlocked())) return 1;
        if (command == 'T') {
            int letter = next_token();
            if (letter < 'a' || letter > 'z' || !end_of_token(getchar_unlocked())) return 1;
            ++length;
        } else if (command == 'P') {
            int position;
            if (!number(position) || position >= length) return 1;
            ++accesses;
            if (previous >= 0) {
                int distance = std::max(previous, position) - std::min(previous, position);
                position_bits += std::bit_width(static_cast<unsigned>(distance));
            }
            previous = position;
            int page = position / 512;
            auto slot = std::find(recent.begin(), recent.end(), page);
            if (slot == recent.end()) {
                ++misses;
                slot = recent.end() - 1;
            }
            std::move_backward(recent.begin(), slot, slot + 1);
            recent.front() = page;
        } else {
            return 1;
        }
    }
    if (next_token() != EOF || std::ferror(stdin)) return 1;
    std::printf("%d %d %lld\n", accesses, misses, position_bits);
}

// Scan the text grader input once, tracking LRU misses and the last queried position per page.
#include <algorithm>
#include <array>
#include <bit>
#include <cctype>
#include <climits>
#include <cstdio>
#include <cstdlib>
#include <vector>

class Tokens {
    int ch = getchar_unlocked();

    void skip_space() {
        while (std::isspace(ch)) ch = getchar_unlocked();
    }

public:
    int letter() {
        skip_space();
        int value = ch;
        ch = getchar_unlocked();
        return ch == EOF || std::isspace(ch) ? value : -1;
    }

    int number() {
        skip_space();
        if (ch < '0' || ch > '9') return -1;
        int value = 0;
        while (ch >= '0' && ch <= '9') {
            int digit = ch - '0';
            if (value > (INT_MAX - digit) / 10) return -1;
            value = value * 10 + digit;
            ch = getchar_unlocked();
        }
        return ch == EOF || std::isspace(ch) ? value : -1;
    }

    bool finished() {
        skip_space();
        return ch == EOF && !std::ferror(stdin);
    }
};

int main(int argc, char**) {
    if (argc != 1) return 1;
    Tokens in;
    int commands = in.number();
    if (commands < 0) return 1;
    std::array<int, 16> recent;
    recent.fill(-1);
    std::vector<int> last;
    int length = 0, accesses = 0, misses = 0, disruptive = 0, previous = -1;
    long long position_bits = 0;
    for (int i = 0; i < commands; ++i) {
        int command = in.letter();
        if (command == 'T') {
            int ch = in.letter();
            if (ch < 'a' || ch > 'z') return 1;
            if (length % 512 == 0) last.push_back(-1);
            ++length;
        } else if (command == 'P') {
            int position = in.number();
            if (position < 0 || position >= length) return 1;
            ++accesses;
            if (previous >= 0)
                position_bits += std::bit_width(static_cast<unsigned>(std::abs(position - previous)));
            previous = position;
            int page = position / 512;
            auto slot = std::find(recent.begin(), recent.end(), page);
            if (slot == recent.end()) {
                ++misses;
                if (last[page] == -1 || std::abs(position - last[page]) > 8) ++disruptive;
                slot = recent.end() - 1;
            }
            std::move_backward(recent.begin(), slot, slot + 1);
            recent.front() = page;
            last[page] = position;
        } else {
            return 1;
        }
    }
    if (!in.finished()) return 1;
    std::printf("%d %d %d %lld\n", accesses, misses, disruptive, position_bits);
}

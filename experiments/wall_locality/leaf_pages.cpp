// Count cold 16-page LRU misses on the left and right endpoints of IOI Wall updates.
#include <algorithm>
#include <array>
#include <cctype>
#include <climits>
#include <cstdio>

static int next_nonspace() {
    int ch;
    do {
        ch = getchar_unlocked();
    } while (std::isspace(ch));
    return ch;
}

static bool read_number(int& value) {
    int ch = next_nonspace();
    if (ch == '+') ch = getchar_unlocked();
    if (ch < '0' || ch > '9') return false;
    value = 0;
    do {
        int digit = ch - '0';
        if (value > (INT_MAX - digit) / 10) return false;
        value = value * 10 + digit;
        ch = getchar_unlocked();
    } while (ch >= '0' && ch <= '9');
    return ch == EOF || std::isspace(ch);
}

class LeafPages {
    static constexpr int cells_per_page = 512;
    std::array<int, 16> recent;

public:
    LeafPages() { recent.fill(-1); }

    bool miss(int cell) {
        int page = cell / cells_per_page;
        auto slot = std::find(recent.begin(), recent.end(), page);
        bool absent = slot == recent.end();
        if (absent) slot = recent.end() - 1;
        std::move_backward(recent.begin(), slot, slot + 1);
        recent.front() = page;
        return absent;
    }
};

int main(int argc, char**) {
    int n, k;
    if (argc != 1 || !read_number(n) || !read_number(k) ||
        n < 1 || n > 2000000 || k < 1 || k > 500000) return 1;

    LeafPages pages;
    int misses = 0;
    for (int i = 0; i < k; ++i) {
        int op, left, right, height;
        if (!read_number(op) || !read_number(left) || !read_number(right) || !read_number(height) ||
            (op != 1 && op != 2) || left > right || right >= n || height > 100000) return 1;
        misses += pages.miss(left);
        misses += pages.miss(right);
    }
    if (next_nonspace() != EOF || std::ferror(stdin)) return 1;
    std::printf("%d %d\n", 2 * k, misses);
}

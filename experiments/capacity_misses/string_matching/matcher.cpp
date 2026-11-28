#include <algorithm>
#include <cstdio>
#include <iostream>
#include <string>
#include <vector>

// A literal pattern and text on two lines; count matches at all offsets,
// including overlapping ones. The registered inputs have a nonempty pattern.
int main() {
    std::ios::sync_with_stdio(false);
    std::string pattern, text;
    if (!std::getline(std::cin, pattern) || !std::getline(std::cin, text) || pattern.empty())
        return 1;
    int count = 0;
    int m = static_cast<int>(pattern.size()), n = static_cast<int>(text.size());
#if defined(SOLVER_KMP)
    std::vector<int> prefix(m);
    for (int i = 1, j = 0; i < m; ++i) {
        while (j && pattern[i] != pattern[j]) j = prefix[j - 1];
        if (pattern[i] == pattern[j]) ++j;
        prefix[i] = j;
    }
    int j = 0;
    for (char c : text) {
        while (j && c != pattern[j]) j = prefix[j - 1];
        if (c == pattern[j]) ++j;
        if (j == m) {
            ++count;
            j = prefix[j - 1];
        }
    }
#elif defined(SOLVER_Z)
    // The input alphabet a..p excludes this separator.
    std::string joined = pattern + '#' + text;
    int size = static_cast<int>(joined.size());
    std::vector<int> z(size);
    int left = 0, right = 0;
    for (int i = 1; i < size; ++i) {
        if (i < right) z[i] = std::min(right - i, z[i - left]);
        while (i + z[i] < size && joined[z[i]] == joined[i + z[i]]) ++z[i];
        if (i + z[i] > right) {
            left = i;
            right = i + z[i];
        }
        if (i > m && z[i] == m) ++count;
    }
#elif defined(SOLVER_NAIVE)
    for (int i = 0; i + m <= n; ++i) {
        int j = 0;
        while (j < m && text[i + j] == pattern[j]) ++j;
        if (j == m) ++count;
    }
#else
#error "Select one matcher"
#endif
    std::printf("%d\n", count);
}

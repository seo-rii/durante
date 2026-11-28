#include <algorithm>
#include <iostream>
#include <string>
#include <vector>

// Length of the longest common subsequence of the two input lines.
int main() {
    std::ios::sync_with_stdio(false);
    std::string a, b;
    if (!std::getline(std::cin, a) || !std::getline(std::cin, b)) return 1;
    const auto rows = a.size() + 1, cols = b.size() + 1;
#if defined(SOLVER_ROLL)
    std::vector<int> previous(cols), current(cols);
    for (size_t i = 1; i < rows; ++i) {
        for (size_t j = 1; j < cols; ++j)
            current[j] = a[i - 1] == b[j - 1] ? previous[j - 1] + 1
                                                : std::max(current[j - 1], previous[j]);
        current.swap(previous);
    }
    std::cout << previous.back() << '\n';
#else
    std::vector<int> table(rows * cols);
    auto at = [&](size_t i, size_t j) -> int& {
#if defined(SOLVER_ROW)
        return table[i * cols + j];
#elif defined(SOLVER_COLUMN)
        return table[j * rows + i];
#else
#error "Select one LCS implementation"
#endif
    };
    for (size_t i = 1; i < rows; ++i)
        for (size_t j = 1; j < cols; ++j)
            at(i, j) = a[i - 1] == b[j - 1] ? at(i - 1, j - 1) + 1
                                               : std::max(at(i, j - 1), at(i - 1, j));
    std::cout << table.back() << '\n';
#endif
}

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <vector>

#if (defined(SOLVER_MERGE) + defined(SOLVER_FENWICK) + defined(SOLVER_SEGTREE)) != 1
#error Select exactly one inversion-count solver
#endif

#if defined(SOLVER_MERGE)
std::int64_t sort_and_count(std::vector<int>& values, std::vector<int>& scratch, int begin, int end) {
    if (end - begin < 2) return 0;
    int middle = begin + (end - begin) / 2;
    std::int64_t count = sort_and_count(values, scratch, begin, middle);
    count += sort_and_count(values, scratch, middle, end);

    int left = begin, right = middle, out = begin;
    while (left < middle && right < end) {
        if (values[left] <= values[right]) {
            scratch[out++] = values[left++];
        } else {
            count += middle - left;
            scratch[out++] = values[right++];
        }
    }
    while (left < middle) scratch[out++] = values[left++];
    while (right < end) scratch[out++] = values[right++];
    std::copy(scratch.begin() + begin, scratch.begin() + end, values.begin() + begin);
    return count;
}

std::int64_t inversions(std::vector<int>& values) {
    std::vector<int> scratch(values.size());
    return sort_and_count(values, scratch, 0, static_cast<int>(values.size()));
}
#elif defined(SOLVER_FENWICK)
std::int64_t inversions(const std::vector<int>& values) {
    if (values.empty()) return 0;
    int highest = *std::max_element(values.begin(), values.end());
    std::vector<int> tree(highest + 1);
    std::int64_t count = 0;
    for (auto it = values.rbegin(); it != values.rend(); ++it) {
        for (int i = *it - 1; i > 0; i -= i & -i) count += tree[i];
        for (int i = *it; i <= highest; i += i & -i) ++tree[i];
    }
    return count;
}
#elif defined(SOLVER_SEGTREE)
std::int64_t inversions(const std::vector<int>& values) {
    if (values.empty()) return 0;
    int highest = *std::max_element(values.begin(), values.end());
    int leaves = 1;
    while (leaves < highest) leaves *= 2;
    std::vector<int> tree(2 * leaves);
    std::int64_t count = 0;
    for (auto it = values.rbegin(); it != values.rend(); ++it) {
        // Left siblings on the path to the root cover exactly the smaller ranks.
        int node = leaves + *it - 1;
        while (node > 1) {
            if (node % 2 == 1) count += tree[node - 1];
            ++tree[node];
            node /= 2;
        }
    }
    return count;
}
#endif

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    constexpr int max_size = 262144;
    int size;
    if (!(std::cin >> size) || size < 0 || size > max_size) return 1;
    std::vector<int> values(size);
    for (int& rank : values) {
        if (!(std::cin >> rank) || rank < 1 || rank > max_size) return 1;
    }
    std::cout << inversions(values) << '\n';
}

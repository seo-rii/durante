#include <algorithm>
#include <array>
#include <bit>
#include <cstdint>
#include <iostream>
#include <utility>
#include <vector>

#if (defined(SOLVER_FENWICK) + defined(SOLVER_MO) + defined(SOLVER_BITSET)) != 1
#error Select exactly one range-distinct algorithm
#endif

constexpr int palette = 256;
struct Query {
    int left, right, index;
};

std::vector<int> solve(const std::vector<int>& a, std::vector<Query> queries) {
    std::vector<int> answers(queries.size());
#if defined(SOLVER_FENWICK)
    std::sort(queries.begin(), queries.end(), [](Query x, Query y) {
        return x.right < y.right;
    });
    const int n = static_cast<int>(a.size());
    std::vector<int> tree(n + 1);
    std::array<int, palette> last{};
    auto add = [&](int i, int delta) {
        for (; i <= n; i += i & -i) tree[i] += delta;
    };
    auto prefix = [&](int i) {
        int count = 0;
        for (; i; i -= i & -i) count += tree[i];
        return count;
    };
    int next = 0;
    for (Query q : queries) {
        while (next <= q.right) {
            int& previous = last[a[next]];
            if (previous) add(previous, -1);
            previous = ++next;
            add(previous, 1);
        }
        answers[q.index] = prefix(q.right + 1) - prefix(q.left);
    }
#elif defined(SOLVER_MO)
    constexpr int side = 181; // about sqrt(32768)
    std::sort(queries.begin(), queries.end(), [](Query x, Query y) {
        int bx = x.left / side, by = y.left / side;
        if (bx != by) return bx < by;
        return bx % 2 ? x.right > y.right : x.right < y.right;
    });
    int left = 0, right = -1, count = 0;
    std::array<int, palette> frequency{};
    auto add = [&](int i) { if (frequency[a[i]]++ == 0) ++count; };
    auto remove = [&](int i) { if (--frequency[a[i]] == 0) --count; };
    for (Query q : queries) {
        while (left > q.left) add(--left);
        while (right < q.right) add(++right);
        while (left < q.left) remove(left++);
        while (right > q.right) remove(right--);
        answers[q.index] = count;
    }
#elif defined(SOLVER_BITSET)
    constexpr int width = 128;
    using Mask = std::array<std::uint64_t, palette / 64>;
    std::vector<Mask> blocks((a.size() + width - 1) / width);
    auto record = [](Mask& mask, int x) { mask[x / 64] |= std::uint64_t{1} << (x % 64); };
    for (int i = 0; i < static_cast<int>(a.size()); ++i) record(blocks[i / width], a[i]);
    for (Query q : queries) {
        Mask mask{};
        int i = q.left;
        while (i <= q.right && i % width) record(mask, a[i++]);
        while (i + width <= q.right + 1) {
            for (std::size_t w = 0; w < mask.size(); ++w) mask[w] |= blocks[i / width][w];
            i += width;
        }
        while (i <= q.right) record(mask, a[i++]);
        for (auto word : mask) answers[q.index] += std::popcount(word);
    }
#endif
    return answers;
}

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    int n, m;
    if (!(std::cin >> n >> m) || n < 1 || n > 32768 || m < 0 || m > 32768) return 1;
    std::vector<int> a(n);
    for (int& v : a) if (!(std::cin >> v) || v < 0 || v >= palette) return 1;
    std::vector<Query> queries(m);
    for (int i = 0; i < m; ++i) {
        Query& q = queries[i];
        if (!(std::cin >> q.left >> q.right) || q.left < 0 || q.left > q.right || q.right >= n) return 1;
        q.index = i;
    }
    for (int count : solve(a, std::move(queries))) std::cout << count << '\n';
}

#include <algorithm>
#include <bit>
#include <iostream>
#include <set>
#include <utility>
#include <vector>

// Input prices belong to [1, max_price]. A query for budget b removes and
// returns the largest available price <= b, or returns -1 if there is none.
constexpr int max_price = 131072;

#if defined(SOLVER_DSU)
class Inventory {
    std::vector<int> prices, parent;

    int find(int i) {
        while (parent[i] != i) {
            parent[i] = parent[parent[i]];
            i = parent[i];
        }
        return i;
    }

public:
    explicit Inventory(std::vector<int> initial) : prices(std::move(initial)), parent(prices.size() + 1) {
        std::sort(prices.begin(), prices.end());
        for (int i = 0; i < static_cast<int>(parent.size()); ++i) parent[i] = i;
    }
    int take(int budget) {
        int i = find(std::upper_bound(prices.begin(), prices.end(), budget) - prices.begin());
        if (i == 0) return -1;
        parent[i] = find(i - 1);
        return prices[i - 1];
    }
};
#elif defined(SOLVER_MULTISET)
class Inventory {
    std::multiset<int> prices;

public:
    explicit Inventory(const std::vector<int>& initial) : prices(initial.begin(), initial.end()) {}
    int take(int budget) {
        auto it = prices.upper_bound(budget);
        if (it == prices.begin()) return -1;
        --it;
        int value = *it;
        prices.erase(it);
        return value;
    }
};
#elif defined(SOLVER_FENWICK)
class Inventory {
    std::vector<int> count;
    void add(int i, int delta) {
        for (; i <= max_price; i += i & -i) count[i] += delta;
    }

public:
    explicit Inventory(const std::vector<int>& initial) : count(max_price + 1) {
        for (int price : initial) add(price, 1);
    }
    int take(int budget) {
        int rank = 0;
        for (int i = std::min(budget, max_price); i > 0; i -= i & -i) rank += count[i];
        if (rank == 0) return -1;
        int below = 0;
        for (int bit = std::bit_floor(static_cast<unsigned>(max_price)); bit > 0; bit /= 2) {
            int next = below + bit;
            if (next <= max_price && count[next] < rank) {
                rank -= count[next];
                below = next;
            }
        }
        add(below + 1, -1);
        return below + 1;
    }
};
#else
#error Select an inventory implementation
#endif

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    int n, q;
    if (!(std::cin >> n >> q)) return 1;
    std::vector<int> prices(n);
    for (int& price : prices) std::cin >> price;
    Inventory inventory(std::move(prices));
    for (int i = 0; i < q; ++i) {
        int budget;
        if (!(std::cin >> budget)) return 1;
        std::cout << inventory.take(budget) << '\n';
    }
}

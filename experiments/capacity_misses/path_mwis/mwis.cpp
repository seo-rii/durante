#include <algorithm>
#include <cstdint>
#include <iostream>
#include <vector>

using Weight = std::int64_t;

int main() {
    int count;
    std::uint32_t state;
    if (!(std::cin >> count >> state) || count <= 0) return 1;
    std::vector<int> path(count);
    for (int& value : path) {
        state ^= state << 13;
        state ^= state >> 17;
        state ^= state << 5;
        value = static_cast<int>(state % 1001) - 250;
    }

#if defined(SOLVER_ROLL)
    Weight older = 0, best = 0;
    for (int w : path) {
        const Weight next = std::max(best, older + w);
        older = best;
        best = next;
    }
#elif defined(SOLVER_DELTA)
    Weight gain = 0, best = 0;
    for (int w : path) {
        gain = std::max<Weight>(0, w - gain);
        best += gain;
    }
#elif defined(SOLVER_TABLE)
    std::vector<Weight> dp(path.size() + 2, 0);
    for (std::size_t i = 0; i < path.size(); ++i)
        dp[i + 2] = std::max(dp[i + 1], dp[i] + path[i]);
    const Weight best = dp.back();
#else
#error "Select a solver"
#endif
    std::cout << best << '\n';
}

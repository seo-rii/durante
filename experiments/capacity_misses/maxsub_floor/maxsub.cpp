#include <algorithm>
#include <cstdint>
#include <iostream>
#include <limits>
#include <vector>

using Score = std::int64_t;

#if defined(SOLVER_DIVIDE)
static Score divide(const std::vector<int>& values, int start, int end) {
    if (end - start == 1) return values[start];
    int mid = start + (end - start) / 2;
    Score sum = 0, suffix = std::numeric_limits<Score>::lowest();
    for (int i = mid - 1; i >= start; --i) {
        sum += values[i];
        suffix = std::max(suffix, sum);
    }
    sum = 0;
    Score prefix = std::numeric_limits<Score>::lowest();
    for (int i = mid; i < end; ++i) {
        sum += values[i];
        prefix = std::max(prefix, sum);
    }
    return std::max({suffix + prefix, divide(values, start, mid), divide(values, mid, end)});
}
#endif

int main() {
    int count;
    std::uint32_t state;
    if (!(std::cin >> count >> state) || count < 1) return 1;
    std::vector<int> values(count);
    for (int& x : values) {
        state ^= state << 13;
        state ^= state >> 17;
        state ^= state << 5;
        x = static_cast<int>(state % 1001) - 500;
    }
#if defined(SOLVER_KADANE)
    Score ending = values.front(), answer = ending;
    for (int i = 1; i < count; ++i) {
        ending = std::max<Score>(values[i], ending + values[i]);
        answer = std::max(answer, ending);
    }
#elif defined(SOLVER_PREFIX)
    Score sum = 0, low = 0, answer = std::numeric_limits<Score>::lowest();
    for (int x : values) {
        sum += x;
        answer = std::max(answer, sum - low);
        low = std::min(low, sum);
    }
#elif defined(SOLVER_DIVIDE)
    Score answer = divide(values, 0, count);
#else
#error "Select a solver"
#endif
    std::cout << answer << '\n';
}

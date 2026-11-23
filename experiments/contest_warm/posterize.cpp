// ICPC World Finals 2017, Posterize. Minimize squared error over contiguous intensity groups.
#include <algorithm>
#include <cstdint>
#include <iostream>
#include <limits>
#include <vector>

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    int levels, colors;
    if (!(std::cin >> levels >> colors)) return 1;
    std::vector<int> intensity(levels), pixels(levels);
    for (int i = 0; i < levels; ++i) std::cin >> intensity[i] >> pixels[i];

    using Cost = std::int64_t;
    std::vector<std::vector<Cost>> error(levels + 1, std::vector<Cost>(levels));
    for (int end = 1; end <= levels; ++end) {
        Cost weight = 0, sum = 0, squares = 0;
        for (int start = end - 1; start >= 0; --start) {
            Cost value = intensity[start];
            weight += pixels[start];
            sum += pixels[start] * value;
            squares += pixels[start] * value * value;
            // A nearest integer to the segment's weighted mean minimizes its squared loss.
            Cost center = (sum + weight / 2) / weight;
            error[end][start] = squares - 2 * center * sum + center * center * weight;
        }
    }
    constexpr Cost inf = std::numeric_limits<Cost>::max() / 2;
    std::vector<Cost> previous(levels + 1, inf), next(levels + 1, inf);
    previous[0] = 0;
    for (int groups = 1; groups <= colors; ++groups) {
        std::fill(next.begin(), next.end(), inf);
        for (int end = groups; end <= levels; ++end) {
            for (int start = groups - 1; start < end; ++start) {
                next[end] = std::min(next[end], previous[start] + error[end][start]);
            }
        }
        previous.swap(next);
    }
    std::cout << previous[levels] << '\n';
}

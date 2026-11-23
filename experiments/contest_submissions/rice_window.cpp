// IOI 2011 Rice Hub, scanning the sorted fields once with a sliding window.
#include <algorithm>
#include <iostream>
#include <vector>

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    int fields, road_length;
    long long budget;
    if (!(std::cin >> fields >> road_length >> budget)) return 1;

    std::vector<long long> prefix(fields + 1);
    int left = 0, longest = 0;
    for (int end = 1; end <= fields; ++end) {
        int position;
        if (!(std::cin >> position)) return 1;
        prefix[end] = prefix[end - 1] + position;
        for (;;) {
            // Pair the outer fields. Each pair pays its separation to any median.
            int pairs = (end - left) / 2;
            long long upper = prefix[end] - prefix[end - pairs];
            long long lower = prefix[left + pairs] - prefix[left];
            if (upper - lower <= budget) break;
            ++left;
        }
        longest = std::max(longest, end - left);
    }
    std::cout << longest << '\n';
}

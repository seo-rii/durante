// IOI 2011 Rice Hub. The driver reads the fields and prints the besthub return value.
#include <iostream>
#include <vector>

int besthub(int fields, int /*road_length*/, int positions[], long long budget) {
    std::vector<long long> prefix(fields + 1);
    for (int i = 0; i < fields; ++i) {
        prefix[i + 1] = prefix[i] + positions[i];
    }
    // For a fixed hub, the cheapest k fields form a contiguous sorted window.
    // A median minimizes that window's total distance.
    auto affordable = [&](int count) {
        for (int first = 0; first + count <= fields; ++first) {
            int end = first + count;
            int median = first + count / 2;
            long long hub = positions[median];
            long long left = hub * (median - first) - (prefix[median] - prefix[first]);
            long long right = prefix[end] - prefix[median] - hub * (end - median);
            if (left + right <= budget) {
                return true;
            }
        }
        return false;
    };
    int low = 1, high = fields;
    while (low < high) {
        int count = low + (high - low + 1) / 2;
        if (affordable(count)) {
            low = count;
        } else {
            high = count - 1;
        }
    }
    return low;
}

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    int fields, road_length;
    long long budget;
    if (!(std::cin >> fields >> road_length >> budget)) {
        return 1;
    }
    std::vector<int> positions(fields);
    for (int& position : positions) {
        std::cin >> position;
    }
    std::cout << besthub(fields, road_length, positions.data(), budget) << '\n';
}

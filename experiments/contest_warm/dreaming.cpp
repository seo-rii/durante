// IOI 2013 Dreaming, with a stdin/stdout driver for the sample grader's forest format.
#include <algorithm>
#include <functional>
#include <iostream>
#include <utility>
#include <vector>

int travelTime(int n, int m, int link_length, int a[], int b[], int lengths[]) {
    std::vector<std::vector<std::pair<int, int>>> forest(n);
    for (int i = 0; i < m; ++i) {
        forest[a[i]].emplace_back(b[i], lengths[i]);
        forest[b[i]].emplace_back(a[i], lengths[i]);
    }
    std::vector<int> parent(n, -2), distance(n), stack;
    auto farthest = [&](int root) {
        stack.push_back(root);
        parent[root] = -1;
        distance[root] = 0;
        int best = root;
        while (!stack.empty()) {
            int v = stack.back();
            stack.pop_back();
            if (distance[v] > distance[best]) best = v;
            for (auto [next, length] : forest[v]) {
                if (next == parent[v]) continue;
                parent[next] = v;
                distance[next] = distance[v] + length;
                stack.push_back(next);
            }
        }
        return best;
    };
    int result = 0;
    std::vector<int> radii;
    for (int root = 0; root < n; ++root) {
        if (parent[root] != -2) continue;
        int start = farthest(root);
        int end = farthest(start);
        int diameter = distance[end];
        int radius = diameter;
        // The minimax vertex lies on the diameter path. Edge interiors are ineligible.
        for (int v = end; v != -1; v = parent[v]) {
            radius = std::min(radius, std::max(distance[v], diameter - distance[v]));
        }
        radii.push_back(radius);
        result = std::max(result, diameter);
    }
    // Join all component centers to the center of the largest-radius component.
    std::sort(radii.begin(), radii.end(), std::greater<int>());
    if (radii.size() >= 2) result = std::max(result, radii[0] + link_length + radii[1]);
    if (radii.size() >= 3) result = std::max(result, radii[1] + 2 * link_length + radii[2]);
    return result;
}

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    int n, m, link_length;
    if (!(std::cin >> n >> m >> link_length)) return 1;
    std::vector<int> a(m), b(m), lengths(m);
    for (int i = 0; i < m; ++i) std::cin >> a[i] >> b[i] >> lengths[i];
    std::cout << travelTime(n, m, link_length, a.data(), b.data(), lengths.data()) << '\n';
}

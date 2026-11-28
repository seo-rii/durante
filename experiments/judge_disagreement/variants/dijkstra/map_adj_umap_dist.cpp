// Dijkstra over std::map<int, vector<pair>> adjacency and unordered_map
// distances/visited flags (hash and tree lookups instead of arrays).
#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int n, m;
    cin >> n >> m;
    map<int, vector<pair<int, int>>> adj;
    for (int i = 0; i < m; ++i) {
        int u, v, w;
        cin >> u >> v >> w;
        adj[u].push_back({v, w});
    }
    unordered_map<int, long long> dist;
    unordered_set<int> done;
    priority_queue<pair<long long, int>, vector<pair<long long, int>>, greater<>> pq;
    dist[1] = 0;
    pq.push({0, 1});
    long long sum = 0;
    while (!pq.empty()) {
        auto [d, u] = pq.top();
        pq.pop();
        if (done.count(u)) continue;
        done.insert(u);
        sum += d;
        auto it = adj.find(u);
        if (it == adj.end()) continue;
        for (auto [v, w] : it->second) {
            auto jt = dist.find(v);
            if (jt == dist.end() || d + w < jt->second) { dist[v] = d + w; pq.push({d + w, v}); }
        }
    }
    auto it = dist.find(n);
    cout << (it == dist.end() ? -1 : it->second) << ' ' << done.size() << ' ' << sum << '\n';
}

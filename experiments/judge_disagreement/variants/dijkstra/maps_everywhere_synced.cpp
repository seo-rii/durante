// "Everything in a map": std::map adjacency, std::map distances, std::set as
// the priority queue (erase + reinsert), cin left synced with stdio.
#include <bits/stdc++.h>
using namespace std;

int main() {
    int n, m;
    cin >> n >> m;
    map<int, vector<pair<int, int>>> adj;
    for (int i = 0; i < m; ++i) {
        int u, v, w;
        cin >> u >> v >> w;
        adj[u].push_back({v, w});
    }
    map<int, long long> dist;
    set<int> done;
    set<pair<long long, int>> pq;
    dist[1] = 0;
    pq.insert({0, 1});
    long long sum = 0;
    while (!pq.empty()) {
        auto [d, u] = *pq.begin();
        pq.erase(pq.begin());
        done.insert(u);
        sum += d;
        for (auto [v, w] : adj[u]) {
            if (done.count(v)) continue;
            auto it = dist.find(v);
            if (it == dist.end()) { dist[v] = d + w; pq.insert({d + w, v}); }
            else if (d + w < it->second) { pq.erase({it->second, v}); it->second = d + w; pq.insert({d + w, v}); }
        }
    }
    cout << (dist.count(n) ? dist[n] : -1) << ' ' << done.size() << ' ' << sum << endl;
}

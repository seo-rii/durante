// Standard lazy-deletion Dijkstra, but scanf input and a vector<vector<pair>>
// adjacency list (one small heap block per vertex).
#include <bits/stdc++.h>
using namespace std;

int main() {
    int n, m;
    if (scanf("%d %d", &n, &m) != 2) return 1;
    vector<vector<pair<int, int>>> adj(n + 1);
    for (int i = 0; i < m; ++i) {
        int u, v, w;
        if (scanf("%d %d %d", &u, &v, &w) != 3) return 1;
        adj[u].emplace_back(v, w);
    }
    const long long INF = LLONG_MAX;
    vector<long long> dist(n + 1, INF);
    vector<bool> done(n + 1, false);
    priority_queue<pair<long long, int>, vector<pair<long long, int>>, greater<>> pq;
    dist[1] = 0;
    pq.push({0, 1});
    long long reach = 0, sum = 0;
    while (!pq.empty()) {
        auto [d, u] = pq.top();
        pq.pop();
        if (done[u]) continue;
        done[u] = true;
        reach++;
        sum += d;
        for (auto &[v, w] : adj[u])
            if (d + w < dist[v]) { dist[v] = d + w; pq.push({dist[v], v}); }
    }
    printf("%lld %lld %lld\n", dist[n] == INF ? -1 : dist[n], reach, sum);
}

// Lazy-deletion binary-heap Dijkstra over vector<vector<pair>> adjacency (one
// heap block per vertex, grown by push_back) with a vector<bool> done flag and
// a getchar_unlocked reader.
#include <bits/stdc++.h>
using namespace std;

static inline int readInt() {
    int c = getchar_unlocked();
    while (c < '0' || c > '9') c = getchar_unlocked();
    int x = 0;
    while (c >= '0' && c <= '9') { x = x * 10 + (c - '0'); c = getchar_unlocked(); }
    return x;
}

int main() {
    int n = readInt(), m = readInt();
    vector<vector<pair<int, int>>> adj(n + 1);
    for (int i = 0; i < m; ++i) {
        int u = readInt(), v = readInt(), w = readInt();
        adj[u].push_back({v, w});
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

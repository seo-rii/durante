// Dijkstra with std::set as an indexed priority queue (erase + reinsert on each
// improvement), vector<vector<pair>> adjacency, getchar_unlocked reader.
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
    set<pair<long long, int>> s;
    dist[1] = 0;
    s.insert({0, 1});
    long long reach = 0, sum = 0;
    while (!s.empty()) {
        auto [d, u] = *s.begin();
        s.erase(s.begin());
        reach++;
        sum += d;
        for (auto [v, w] : adj[u]) {
            if (d + w < dist[v]) {
                if (dist[v] != INF) s.erase({dist[v], v});
                dist[v] = d + w;
                s.insert({dist[v], v});
            }
        }
    }
    printf("%lld %lld %lld\n", dist[n] == INF ? -1 : dist[n], reach, sum);
}

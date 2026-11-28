// Bellman-Ford over the raw edge list with early exit: repeated sequential
// sweeps over all edges with random reads/writes into dist.
#include <bits/stdc++.h>
using namespace std;

static char buf[1 << 16];
static size_t len = 0, pos = 0;
static inline int gc() {
    if (pos == len) { len = fread(buf, 1, sizeof buf, stdin); pos = 0; if (!len) return -1; }
    return buf[pos++];
}
static inline int readInt() {
    int c = gc();
    while (c < '0' || c > '9') c = gc();
    int x = 0;
    while (c >= '0' && c <= '9') { x = x * 10 + (c - '0'); c = gc(); }
    return x;
}

struct Edge { int u, v, w; };

int main() {
    int n = readInt(), m = readInt();
    vector<Edge> e(m);
    for (auto &x : e) { x.u = readInt(); x.v = readInt(); x.w = readInt(); }
    const long long INF = LLONG_MAX;
    vector<long long> dist(n + 1, INF);
    dist[1] = 0;
    for (int round = 1; round < n; ++round) {
        bool changed = false;
        for (const Edge &x : e) {
            long long du = dist[x.u];
            if (du != INF && du + x.w < dist[x.v]) { dist[x.v] = du + x.w; changed = true; }
        }
        if (!changed) break;
    }
    long long reach = 0, sum = 0;
    for (int v = 1; v <= n; ++v) if (dist[v] != INF) { reach++; sum += dist[v]; }
    printf("%lld %lld %lld\n", dist[n] == INF ? -1 : dist[n], reach, sum);
}

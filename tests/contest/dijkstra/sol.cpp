#include <bits/stdc++.h>
using namespace std;

static char buf[1 << 16];
static size_t len = 0, pos = 0;
static inline int gc() {
    if (pos == len) { len = fread(buf, 1, sizeof buf, stdin); pos = 0; if (!len) return -1; }
    return buf[pos++];
}
static inline long long readInt() {
    int c = gc();
    while (c != '-' && (c < '0' || c > '9')) c = gc();
    bool neg = c == '-';
    if (neg) c = gc();
    long long x = 0;
    while (c >= '0' && c <= '9') { x = x * 10 + (c - '0'); c = gc(); }
    return neg ? -x : x;
}

int main() {
    int n = readInt(), m = readInt();
    vector<int> eu(m), ev(m), ew(m), start(n + 2, 0);
    for (int i = 0; i < m; ++i) {
        eu[i] = readInt(); ev[i] = readInt(); ew[i] = readInt();
        start[eu[i] + 1]++;
    }
    for (int i = 1; i <= n + 1; ++i) start[i] += start[i - 1];
    vector<int> to(m), wt(m), fill(start.begin(), start.end());
    for (int i = 0; i < m; ++i) { int p = fill[eu[i]]++; to[p] = ev[i]; wt[p] = ew[i]; }
    const long long INF = LLONG_MAX;
    vector<long long> dist(n + 1, INF);
    priority_queue<pair<long long, int>, vector<pair<long long, int>>, greater<>> pq;
    dist[1] = 0;
    pq.push({0, 1});
    long long reach = 0, sum = 0;
    while (!pq.empty()) {
        auto [d, u] = pq.top();
        pq.pop();
        if (d != dist[u]) continue;
        reach++;
        sum += d;
        for (int p = start[u]; p < start[u + 1]; ++p) {
            long long nd = d + wt[p];
            if (nd < dist[to[p]]) { dist[to[p]] = nd; pq.push({nd, to[p]}); }
        }
    }
    printf("%lld %lld %lld\n", dist[n] == INF ? -1 : dist[n], reach, sum);
}

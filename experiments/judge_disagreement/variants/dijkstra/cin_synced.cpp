// The intended CSR + binary-heap Dijkstra, but reading with cin while it is
// still synchronised with stdio (no sync_with_stdio(false)); answer via endl.
#include <bits/stdc++.h>
using namespace std;

int main() {
    int n, m;
    cin >> n >> m;
    vector<int> eu(m), ev(m), ew(m), start(n + 2, 0);
    for (int i = 0; i < m; ++i) {
        cin >> eu[i] >> ev[i] >> ew[i];
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
    cout << (dist[n] == INF ? -1 : dist[n]) << ' ' << reach << ' ' << sum << endl;
}

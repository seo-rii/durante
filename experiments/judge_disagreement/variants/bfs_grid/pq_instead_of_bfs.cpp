// Dijkstra with a binary heap on a unit-weight grid (the "always use Dijkstra" habit).
#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int h, w;
    cin >> h >> w;
    vector<string> g(h);
    for (auto &row : g) cin >> row;
    const int INF = INT_MAX;
    vector<vector<int>> dist(h, vector<int>(w, INF));
    priority_queue<tuple<int, int, int>, vector<tuple<int, int, int>>, greater<>> pq;
    if (g[0][0] == '.') { dist[0][0] = 0; pq.emplace(0, 0, 0); }
    long long reach = 0;
    int dr[] = {1, -1, 0, 0}, dc[] = {0, 0, 1, -1};
    while (!pq.empty()) {
        auto [d, r, c] = pq.top();
        pq.pop();
        if (d > dist[r][c]) continue;
        ++reach;
        for (int k = 0; k < 4; ++k) {
            int nr = r + dr[k], nc = c + dc[k];
            if (nr < 0 || nr >= h || nc < 0 || nc >= w || g[nr][nc] != '.') continue;
            if (d + 1 < dist[nr][nc]) { dist[nr][nc] = d + 1; pq.emplace(d + 1, nr, nc); }
        }
    }
    cout << (dist[h - 1][w - 1] == INF ? -1 : dist[h - 1][w - 1]) << ' ' << reach << '\n';
}

// BFS with a std::map<pair<int,int>,int> for distances: every lookup is a
// red-black-tree walk over millions of nodes (cache-hostile pointer chasing).
#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int h, w;
    cin >> h >> w;
    vector<string> g(h);
    for (auto &row : g) cin >> row;
    map<pair<int, int>, int> dist;
    queue<pair<int, int>> q;
    if (g[0][0] == '.') { dist[{0, 0}] = 0; q.push({0, 0}); }
    int dr[] = {1, -1, 0, 0}, dc[] = {0, 0, 1, -1};
    while (!q.empty()) {
        auto [r, c] = q.front();
        q.pop();
        int d = dist[{r, c}];
        for (int k = 0; k < 4; ++k) {
            int nr = r + dr[k], nc = c + dc[k];
            if (nr < 0 || nr >= h || nc < 0 || nc >= w || g[nr][nc] != '.') continue;
            if (dist.find({nr, nc}) != dist.end()) continue;
            dist[{nr, nc}] = d + 1;
            q.push({nr, nc});
        }
    }
    auto it = dist.find({h - 1, w - 1});
    cout << (it == dist.end() ? -1 : it->second) << ' ' << dist.size() << '\n';
}

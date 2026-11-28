// BFS with distances kept in an unordered_map keyed by cell id (hash lookups,
// node allocations and rehashing instead of a flat array).
#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int h, w;
    cin >> h >> w;
    vector<string> g(h);
    for (auto &row : g) cin >> row;
    unordered_map<int, int> dist;
    queue<int> q;
    if (g[0][0] == '.') { dist[0] = 0; q.push(0); }
    int dr[] = {1, -1, 0, 0}, dc[] = {0, 0, 1, -1};
    while (!q.empty()) {
        int cur = q.front();
        q.pop();
        int r = cur / w, c = cur % w, d = dist[cur];
        for (int k = 0; k < 4; ++k) {
            int nr = r + dr[k], nc = c + dc[k];
            if (nr < 0 || nr >= h || nc < 0 || nc >= w || g[nr][nc] != '.') continue;
            int id = nr * w + nc;
            if (dist.count(id)) continue;
            dist[id] = d + 1;
            q.push(id);
        }
    }
    auto it = dist.find(h * w - 1);
    cout << (it == dist.end() ? -1 : it->second) << ' ' << dist.size() << '\n';
}

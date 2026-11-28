// BFS with an unordered_set<long long> of visited cells plus an
// unordered_map<long long,int> of distances: two hash tables, no flat arrays.
#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int h, w;
    cin >> h >> w;
    vector<string> g(h);
    for (auto &row : g) cin >> row;
    auto key = [&](long long r, long long c) { return r * w + c; };
    unordered_set<long long> seen;
    unordered_map<long long, int> dist;
    queue<pair<int, int>> q;
    if (g[0][0] == '.') { seen.insert(key(0, 0)); dist[key(0, 0)] = 0; q.push({0, 0}); }
    int dr[] = {1, -1, 0, 0}, dc[] = {0, 0, 1, -1};
    while (!q.empty()) {
        auto [r, c] = q.front();
        q.pop();
        int d = dist[key(r, c)];
        for (int k = 0; k < 4; ++k) {
            int nr = r + dr[k], nc = c + dc[k];
            if (nr < 0 || nr >= h || nc < 0 || nc >= w || g[nr][nc] != '.') continue;
            if (!seen.insert(key(nr, nc)).second) continue;
            dist[key(nr, nc)] = d + 1;
            q.push({nr, nc});
        }
    }
    auto it = dist.find(key(h - 1, w - 1));
    cout << (it == dist.end() ? -1 : it->second) << ' ' << seen.size() << '\n';
}

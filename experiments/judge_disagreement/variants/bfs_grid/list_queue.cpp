// BFS whose queue is a std::list<pair<int,int>> (one heap node per visited cell)
// with a bit-packed vector<vector<bool>> visited array next to the int distances.
#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int h, w;
    cin >> h >> w;
    vector<string> g(h);
    for (auto &row : g) cin >> row;
    vector<vector<bool>> seen(h, vector<bool>(w, false));
    vector<vector<int>> dist(h, vector<int>(w, -1));
    list<pair<int, int>> q;
    long long reach = 0;
    if (g[0][0] == '.') { seen[0][0] = true; dist[0][0] = 0; q.push_back({0, 0}); reach = 1; }
    while (!q.empty()) {
        auto [r, c] = q.front();
        q.pop_front();
        auto visit = [&](int nr, int nc) {
            if (g[nr][nc] != '.' || seen[nr][nc]) return;
            seen[nr][nc] = true;
            dist[nr][nc] = dist[r][c] + 1;
            q.push_back({nr, nc});
            ++reach;
        };
        if (r + 1 < h) visit(r + 1, c);
        if (r > 0) visit(r - 1, c);
        if (c + 1 < w) visit(r, c + 1);
        if (c > 0) visit(r, c - 1);
    }
    cout << dist[h - 1][w - 1] << ' ' << reach << '\n';
}

// Textbook BFS: std::queue<pair<int,int>> (deque-backed), vector<vector<int>> dist,
// separate bounds checks per direction.
#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int h, w;
    cin >> h >> w;
    vector<string> g(h);
    for (auto &row : g) cin >> row;
    vector<vector<int>> dist(h, vector<int>(w, -1));
    queue<pair<int, int>> q;
    long long reach = 0;
    if (g[0][0] == '.') { dist[0][0] = 0; q.push({0, 0}); reach = 1; }
    int dr[] = {1, -1, 0, 0}, dc[] = {0, 0, 1, -1};
    while (!q.empty()) {
        auto [r, c] = q.front();
        q.pop();
        for (int k = 0; k < 4; ++k) {
            int nr = r + dr[k], nc = c + dc[k];
            if (nr >= 0 && nr < h && nc >= 0 && nc < w && g[nr][nc] == '.' && dist[nr][nc] == -1) {
                dist[nr][nc] = dist[r][c] + 1;
                q.push({nr, nc});
                ++reach;
            }
        }
    }
    cout << dist[h - 1][w - 1] << ' ' << reach << '\n';
}

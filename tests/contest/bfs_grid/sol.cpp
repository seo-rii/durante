#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int h, w;
    cin >> h >> w;
    vector<string> g(h);
    for (auto &row : g) cin >> row;
    vector<int> dist((size_t)h * w, -1), q((size_t)h * w);
    size_t head = 0, tail = 0;
    if (g[0][0] == '.') { dist[0] = 0; q[tail++] = 0; }
    const int dr[4] = {1, -1, 0, 0}, dc[4] = {0, 0, 1, -1};
    while (head < tail) {
        int cur = q[head++], r = cur / w, c = cur % w;
        for (int k = 0; k < 4; ++k) {
            int nr = r + dr[k], nc = c + dc[k];
            if (nr < 0 || nr >= h || nc < 0 || nc >= w || g[nr][nc] != '.') continue;
            int id = nr * w + nc;
            if (dist[id] != -1) continue;
            dist[id] = dist[cur] + 1;
            q[tail++] = id;
        }
    }
    cout << dist[(size_t)h * w - 1] << ' ' << tail << '\n';
}

// Bit-parallel BFS: each level recomputes the frontier for the whole grid with
// 64-bit word operations (sequential, compute-bound, no queue at all).
#include <bits/stdc++.h>
using namespace std;
typedef unsigned long long u64;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int h, w;
    cin >> h >> w;
    int W = (w + 63) / 64;
    vector<u64> fre((size_t)(h + 2) * W, 0), vis(fre.size(), 0), cur(fre.size(), 0), nxt(fre.size(), 0);
    string row;
    for (int r = 0; r < h; ++r) {
        cin >> row;
        u64 *f = &fre[(size_t)(r + 1) * W];
        for (int c = 0; c < w; ++c)
            if (row[c] == '.') f[c >> 6] |= 1ULL << (c & 63);
    }
    auto bit = [&](vector<u64> &v, int r, int c) { return (v[(size_t)(r + 1) * W + (c >> 6)] >> (c & 63)) & 1; };
    long long reach = 0;
    int goalDist = -1, level = 0;
    if (bit(fre, 0, 0)) { cur[W] = vis[W] = 1; reach = 1; }
    if (h == 1 && w == 1 && reach) goalDist = 0;
    bool any = reach > 0;
    while (any) {
        ++level;
        any = false;
        for (int r = 1; r <= h; ++r) {
            const u64 *up = &cur[(size_t)(r - 1) * W], *mid = &cur[(size_t)r * W], *dn = &cur[(size_t)(r + 1) * W];
            const u64 *f = &fre[(size_t)r * W];
            u64 *vs = &vis[(size_t)r * W], *nx = &nxt[(size_t)r * W];
            for (int k = 0; k < W; ++k) {
                u64 m = mid[k];
                u64 left = (m << 1) | (k ? mid[k - 1] >> 63 : 0);
                u64 right = (m >> 1) | (k + 1 < W ? mid[k + 1] << 63 : 0);
                u64 n = (up[k] | dn[k] | left | right) & f[k] & ~vs[k];
                nx[k] = n;
                if (n) { vs[k] |= n; reach += __builtin_popcountll(n); any = true; }
            }
        }
        if (goalDist < 0 && bit(nxt, h - 1, w - 1)) goalDist = level;
        swap(cur, nxt);
    }
    cout << goalDist << ' ' << reach << '\n';
}

#include <bits/stdc++.h>
using namespace std;

static const long long MOD = 1000000007;
static vector<int> head_, nxt_, to_;
static long long total = 0;
static int maxDepth = 0;

// Plain recursion: depth reaches n, so this needs a large stack rlimit.
static long long dfs(int v, int parent, int depth) {
    if (depth > maxDepth) maxDepth = depth;
    long long size = 1;
    for (int e = head_[v]; e != -1; e = nxt_[e])
        if (to_[e] != parent) size += dfs(to_[e], v, depth + 1);
    total = (total + size % MOD * v) % MOD;
    return size;
}

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int n;
    cin >> n;
    head_.assign(n + 1, -1);
    nxt_.resize(2 * (n - 1));
    to_.resize(2 * (n - 1));
    for (int i = 0, e = 0; i < n - 1; ++i) {
        int u, v;
        cin >> u >> v;
        to_[e] = v; nxt_[e] = head_[u]; head_[u] = e++;
        to_[e] = u; nxt_[e] = head_[v]; head_[v] = e++;
    }
    dfs(1, 0, 0);
    cout << maxDepth << ' ' << total << '\n';
}

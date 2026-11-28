// Adjacency in one std::multimap<int,int> holding both directions of every
// edge; recursive DFS enumerates neighbours with equal_range (a tree search
// over 2(n-1) nodes per visited vertex).
#include <bits/stdc++.h>
using namespace std;

static const long long MOD = 1000000007;
static multimap<int, int> adj;
static long long total = 0;
static int maxDepth = 0;

static long long dfs(int v, int parent, int depth) {
    maxDepth = max(maxDepth, depth);
    long long size = 1;
    auto [lo, hi] = adj.equal_range(v);
    for (auto it = lo; it != hi; ++it)
        if (it->second != parent) size += dfs(it->second, v, depth + 1);
    total = (total + size % MOD * v) % MOD;
    return size;
}

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int n;
    cin >> n;
    for (int i = 0; i < n - 1; ++i) {
        int u, v;
        cin >> u >> v;
        adj.insert({u, v});
        adj.insert({v, u});
    }
    dfs(1, 0, 0);
    cout << maxDepth << ' ' << total << '\n';
}

// Recursive DFS over vector<vector<int>> adjacency (one heap block per vertex),
// scanf input.
#include <bits/stdc++.h>
using namespace std;

static const long long MOD = 1000000007;
static vector<vector<int>> adj;
static long long total = 0;
static int maxDepth = 0;

static long long dfs(int v, int parent, int depth) {
    maxDepth = max(maxDepth, depth);
    long long size = 1;
    for (int u : adj[v])
        if (u != parent) size += dfs(u, v, depth + 1);
    total = (total + size % MOD * v) % MOD;
    return size;
}

int main() {
    int n;
    if (scanf("%d", &n) != 1) return 1;
    adj.resize(n + 1);
    for (int i = 0; i < n - 1; ++i) {
        int u, v;
        if (scanf("%d %d", &u, &v) != 2) return 1;
        adj[u].push_back(v);
        adj[v].push_back(u);
    }
    dfs(1, 0, 0);
    printf("%d %lld\n", maxDepth, total);
}

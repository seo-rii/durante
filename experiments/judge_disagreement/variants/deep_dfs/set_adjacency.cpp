// Recursive DFS over vector<set<int>> adjacency: every neighbour is a separate
// red-black-tree node, iterated by pointer chasing.
#include <bits/stdc++.h>
using namespace std;

static const long long MOD = 1000000007;
static vector<set<int>> adj;
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
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int n;
    cin >> n;
    adj.resize(n + 1);
    for (int i = 0; i < n - 1; ++i) {
        int u, v;
        cin >> u >> v;
        adj[u].insert(v);
        adj[v].insert(u);
    }
    dfs(1, 0, 0);
    cout << maxDepth << ' ' << total << '\n';
}

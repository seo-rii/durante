// Recursive DFS over unordered_map<int, vector<int>> adjacency: a hash lookup
// plus a separately allocated vector per visited vertex.
#include <bits/stdc++.h>
using namespace std;

static const long long MOD = 1000000007;
static unordered_map<int, vector<int>> adj;
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
    for (int i = 0; i < n - 1; ++i) {
        int u, v;
        cin >> u >> v;
        adj[u].push_back(v);
        adj[v].push_back(u);
    }
    dfs(1, 0, 0);
    cout << maxDepth << ' ' << total << '\n';
}

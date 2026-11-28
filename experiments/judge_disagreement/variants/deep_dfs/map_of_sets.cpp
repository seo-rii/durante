// "Graph as a map of sets": std::map<int, std::set<int>> adjacency, a
// std::map<int,long long> of subtree sizes filled during a recursive DFS, and
// cin left synced with stdio.
#include <bits/stdc++.h>
using namespace std;

static const long long MOD = 1000000007;
static map<int, set<int>> adj;
static map<int, long long> sz;
static long long total = 0;
static int maxDepth = 0;

static void dfs(int v, int parent, int depth) {
    maxDepth = max(maxDepth, depth);
    sz[v] = 1;
    for (int u : adj[v])
        if (u != parent) { dfs(u, v, depth + 1); sz[v] += sz[u]; }
    total = (total + sz[v] % MOD * v) % MOD;
}

int main() {
    int n;
    cin >> n;
    for (int i = 0; i < n - 1; ++i) {
        int u, v;
        cin >> u >> v;
        adj[u].insert(v);
        adj[v].insert(u);
    }
    dfs(1, 0, 0);
    cout << maxDepth << ' ' << total << endl;
}

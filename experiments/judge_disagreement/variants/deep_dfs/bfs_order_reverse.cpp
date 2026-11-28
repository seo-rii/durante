// No DFS at all: BFS from the root records an order, parents and depths;
// subtree sizes are then accumulated by walking that order backwards.
#include <bits/stdc++.h>
using namespace std;

static const long long MOD = 1000000007;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int n;
    cin >> n;
    vector<int> head(n + 1, -1), nxt(2 * (n - 1)), to(2 * (n - 1));
    for (int i = 0, e = 0; i < n - 1; ++i) {
        int u, v;
        cin >> u >> v;
        to[e] = v; nxt[e] = head[u]; head[u] = e++;
        to[e] = u; nxt[e] = head[v]; head[v] = e++;
    }
    vector<int> order, parent(n + 1, 0), depth(n + 1, 0);
    order.reserve(n);
    order.push_back(1);
    for (size_t i = 0; i < order.size(); ++i) {
        int v = order[i];
        for (int e = head[v]; e != -1; e = nxt[e])
            if (to[e] != parent[v]) { parent[to[e]] = v; depth[to[e]] = depth[v] + 1; order.push_back(to[e]); }
    }
    vector<long long> size(n + 1, 1);
    long long total = 0;
    int maxDepth = 0;
    for (int i = n - 1; i >= 0; --i) {
        int v = order[i];
        maxDepth = max(maxDepth, depth[v]);
        total = (total + size[v] % MOD * v) % MOD;
        if (parent[v]) size[parent[v]] += size[v];
    }
    cout << maxDepth << ' ' << total << '\n';
}

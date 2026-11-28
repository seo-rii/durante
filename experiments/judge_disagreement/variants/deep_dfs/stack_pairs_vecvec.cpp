// Iterative two-phase DFS: std::stack<pair<int,int>> (deque-backed) produces a
// preorder with parents over vector<vector<int>> adjacency, then sizes are
// accumulated in reverse preorder.
#include <bits/stdc++.h>
using namespace std;

static const long long MOD = 1000000007;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int n;
    cin >> n;
    vector<vector<int>> adj(n + 1);
    for (int i = 0; i < n - 1; ++i) {
        int u, v;
        cin >> u >> v;
        adj[u].push_back(v);
        adj[v].push_back(u);
    }
    vector<int> parent(n + 1, 0), depth(n + 1, 0), order;
    order.reserve(n);
    stack<pair<int, int>> st;
    st.push({1, 0});
    while (!st.empty()) {
        auto [v, p] = st.top();
        st.pop();
        parent[v] = p;
        depth[v] = p ? depth[p] + 1 : 0;
        order.push_back(v);
        for (int u : adj[v])
            if (u != p) st.push({u, v});
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

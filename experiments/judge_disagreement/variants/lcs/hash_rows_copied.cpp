// Rolling-row DP where each row is an unordered_map<int,int> used as a sparse
// array, built fresh for every row and copied into prev (prev = cur), so every
// cell costs hash lookups plus node allocations, rehashes and a copy.
#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    string a, b;
    cin >> a >> b;
    int n = a.size(), m = b.size();
    unordered_map<int, int> prev;
    for (int j = 0; j <= m; ++j) prev[j] = 0;
    for (int i = 1; i <= n; ++i) {
        unordered_map<int, int> cur;
        cur[0] = 0;
        for (int j = 1; j <= m; ++j)
            cur[j] = a[i - 1] == b[j - 1] ? prev[j - 1] + 1 : max(prev[j], cur[j - 1]);
        prev = cur;
    }
    cout << prev[m] << '\n';
}

// Full (n+1) x (m+1) DP table of uint16_t in one flat array (128 MB of
// streaming writes) instead of two rolling rows.
#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    string a, b;
    cin >> a >> b;
    size_t n = a.size(), m = b.size(), W = m + 1;
    vector<uint16_t> dp((n + 1) * W, 0);
    for (size_t i = 1; i <= n; ++i) {
        uint16_t *cur = &dp[i * W], *prev = &dp[(i - 1) * W];
        for (size_t j = 1; j <= m; ++j)
            cur[j] = a[i - 1] == b[j - 1] ? prev[j - 1] + 1 : max(prev[j], cur[j - 1]);
    }
    cout << dp[n * W + m] << '\n';
}

// Full vector<vector<uint16_t>> table filled column by column: the inner loop
// strides across rows, touching a different page on every access.
#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    string a, b;
    cin >> a >> b;
    int n = a.size(), m = b.size();
    vector<vector<uint16_t>> dp(n + 1, vector<uint16_t>(m + 1, 0));
    for (int j = 1; j <= m; ++j)
        for (int i = 1; i <= n; ++i)
            dp[i][j] = a[i - 1] == b[j - 1] ? dp[i - 1][j - 1] + 1 : max(dp[i - 1][j], dp[i][j - 1]);
    cout << dp[n][m] << '\n';
}

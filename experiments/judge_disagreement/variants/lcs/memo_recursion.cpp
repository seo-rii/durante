// Top-down memoised recursion lcs(i, j) over a vector<vector<short>> memo.
#include <bits/stdc++.h>
using namespace std;

static string a, b;
static vector<vector<short>> memo;

static int lcs(int i, int j) {
    if (i == (int)a.size() || j == (int)b.size()) return 0;
    short &r = memo[i][j];
    if (r >= 0) return r;
    if (a[i] == b[j]) return r = lcs(i + 1, j + 1) + 1;
    return r = max(lcs(i + 1, j), lcs(i, j + 1));
}

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    cin >> a >> b;
    memo.assign(a.size(), vector<short>(b.size(), -1));
    cout << lcs(0, 0) << '\n';
}

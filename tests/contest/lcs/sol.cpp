#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    string a, b;
    cin >> a >> b;
    size_t m = b.size();
    vector<int> prev(m + 1, 0), cur(m + 1, 0);
    for (char ca : a) {
        cur[0] = 0;
        for (size_t j = 1; j <= m; ++j)
            cur[j] = ca == b[j - 1] ? prev[j - 1] + 1 : max(prev[j], cur[j - 1]);
        swap(prev, cur);
    }
    cout << prev[m] << '\n';
}

// Z-function over pattern + '#' + text: an int array as long as the text
// (80 MB) and a second linear pass to collect matches.
#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    string t, p;
    cin >> t >> p;
    string s = p + '#' + t;
    int n = s.size(), m = p.size();
    vector<int> z(n, 0);
    for (int i = 1, l = 0, r = 0; i < n; ++i) {
        if (i < r) z[i] = min(r - i, z[i - l]);
        while (i + z[i] < n && s[z[i]] == s[i + z[i]]) ++z[i];
        if (i + z[i] > r) { l = i; r = i + z[i]; }
    }
    long long count = 0, total = 0;
    for (int i = m + 1; i < n; ++i)
        if (z[i] >= m) { ++count; total += i - m; }
    cout << count << ' ' << total << '\n';
}

// Brute force with t.substr(i, m) == p at every position: a heap-allocated
// temporary string (longer than the SSO buffer) per text position.
#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    string t, p;
    cin >> t >> p;
    long long count = 0, total = 0;
    int n = t.size(), m = p.size();
    for (int i = 0; i + m <= n; ++i)
        if (t.substr(i, m) == p) { ++count; total += i + 1; }
    cout << count << ' ' << total << '\n';
}

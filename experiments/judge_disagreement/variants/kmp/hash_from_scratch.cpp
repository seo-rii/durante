// "Rabin-Karp" that recomputes the polynomial hash (mod 1e9+7) of every window
// from scratch, O(n*m) modular multiply-adds; hash hits are verified exactly.
#include <bits/stdc++.h>
using namespace std;

const long long MOD = 1000000007, BASE = 131;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    string t, p;
    cin >> t >> p;
    int n = t.size(), m = p.size();
    auto hashOf = [&](const string &s, int from) {
        long long r = 0;
        for (int k = 0; k < m; ++k) r = (r * BASE + s[from + k]) % MOD;
        return r;
    };
    long long hp = hashOf(p, 0), count = 0, total = 0;
    for (int i = 0; i + m <= n; ++i)
        if (hashOf(t, i) == hp && t.compare(i, m, p) == 0) { ++count; total += i + 1; }
    cout << count << ' ' << total << '\n';
}

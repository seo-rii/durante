// The intended Fenwick tree, but with cin/cout (sync off) and endl after every
// answer: one flush, i.e. one write(2) system call, per query.
#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int n, q;
    cin >> n >> q;
    vector<long long> bit(n + 1, 0);
    for (int i = 1; i <= n; ++i) {
        long long v;
        cin >> v;
        bit[i] += v;
        int j = i + (i & -i);
        if (j <= n) bit[j] += bit[i];
    }
    auto prefix = [&](int i) { long long s = 0; for (; i > 0; i -= i & -i) s += bit[i]; return s; };
    for (int k = 0; k < q; ++k) {
        int t, x;
        long long y;
        cin >> t >> x >> y;
        if (t == 1) {
            for (int i = x; i <= n; i += i & -i) bit[i] += y;
        } else {
            cout << prefix((int)y) - prefix(x - 1) << endl;
        }
    }
}

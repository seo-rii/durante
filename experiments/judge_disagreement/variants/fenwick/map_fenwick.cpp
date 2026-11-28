// The intended Fenwick tree, but stored in a std::map<int,long long> used as a
// sparse array: every one of the ~log n node touches is a red-black-tree lookup
// over 700k nodes instead of an array index.
#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int n, q;
    cin >> n >> q;
    map<int, long long> bit;
    for (int i = 1; i <= n; ++i) {
        long long v;
        cin >> v;
        bit[i] += v;
        int j = i + (i & -i);
        if (j <= n) bit[j] += bit[i];
    }
    auto prefix = [&](int i) { long long s = 0; for (; i > 0; i -= i & -i) s += bit[i]; return s; };
    string out;
    for (int k = 0; k < q; ++k) {
        int t, x;
        long long y;
        cin >> t >> x >> y;
        if (t == 1) {
            for (int i = x; i <= n; i += i & -i) bit[i] += y;
        } else {
            out += to_string(prefix((int)y) - prefix(x - 1));
            out += '\n';
        }
    }
    cout << out;
}

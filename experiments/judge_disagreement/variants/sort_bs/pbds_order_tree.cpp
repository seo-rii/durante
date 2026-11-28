// Order-statistics tree from __gnu_pbds over (value, index) pairs; each query
// is order_of_key, a walk down a pointer-based red-black tree.
#include <bits/stdc++.h>
#include <ext/pb_ds/assoc_container.hpp>
#include <ext/pb_ds/tree_policy.hpp>
using namespace std;
using namespace __gnu_pbds;

typedef tree<pair<int, int>, null_type, less<pair<int, int>>, rb_tree_tag, tree_order_statistics_node_update> ordered_set;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int n, q;
    cin >> n >> q;
    ordered_set s;
    for (int i = 0; i < n; ++i) { int x; cin >> x; s.insert({x, i}); }
    string out;
    for (int i = 0; i < q; ++i) {
        int x;
        cin >> x;
        out += to_string(s.order_of_key({x, INT_MAX}));
        out += '\n';
    }
    cout << out;
}

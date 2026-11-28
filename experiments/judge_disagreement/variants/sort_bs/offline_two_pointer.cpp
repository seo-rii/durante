// Offline: sort the values and the (query, index) pairs, sweep both with a
// two-pointer merge, then print answers in input order. No binary search.
#include <bits/stdc++.h>
using namespace std;

int main() {
    int n, q;
    if (scanf("%d %d", &n, &q) != 2) return 1;
    vector<int> a(n);
    for (auto &x : a) if (scanf("%d", &x) != 1) return 1;
    vector<pair<int, int>> qs(q);
    for (int i = 0; i < q; ++i) { if (scanf("%d", &qs[i].first) != 1) return 1; qs[i].second = i; }
    sort(a.begin(), a.end());
    sort(qs.begin(), qs.end());
    vector<int> ans(q);
    int p = 0;
    for (auto &[x, idx] : qs) {
        while (p < n && a[p] <= x) ++p;
        ans[idx] = p;
    }
    for (int i = 0; i < q; ++i) printf("%d\n", ans[i]);
}

// Classic recursive top-down segment tree (4n array), scanf/printf I/O.
#include <bits/stdc++.h>
using namespace std;

static int n;
static vector<long long> t;

static void build(int node, int l, int r, const vector<long long> &a) {
    if (l == r) { t[node] = a[l]; return; }
    int mid = (l + r) / 2;
    build(2 * node, l, mid, a);
    build(2 * node + 1, mid + 1, r, a);
    t[node] = t[2 * node] + t[2 * node + 1];
}
static void update(int node, int l, int r, int pos, long long val) {
    if (l == r) { t[node] += val; return; }
    int mid = (l + r) / 2;
    if (pos <= mid) update(2 * node, l, mid, pos, val);
    else update(2 * node + 1, mid + 1, r, pos, val);
    t[node] = t[2 * node] + t[2 * node + 1];
}
static long long query(int node, int l, int r, int ql, int qr) {
    if (qr < l || r < ql) return 0;
    if (ql <= l && r <= qr) return t[node];
    int mid = (l + r) / 2;
    return query(2 * node, l, mid, ql, qr) + query(2 * node + 1, mid + 1, r, ql, qr);
}

int main() {
    int q;
    if (scanf("%d %d", &n, &q) != 2) return 1;
    vector<long long> a(n + 1);
    for (int i = 1; i <= n; ++i) if (scanf("%lld", &a[i]) != 1) return 1;
    t.assign(4 * n + 4, 0);
    build(1, 1, n, a);
    for (int k = 0; k < q; ++k) {
        int type, x;
        long long y;
        if (scanf("%d %d %lld", &type, &x, &y) != 3) return 1;
        if (type == 1) update(1, 1, n, x, y);
        else printf("%lld\n", query(1, 1, n, x, (int)y));
    }
}

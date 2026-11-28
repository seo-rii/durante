// Segment tree of heap-allocated nodes linked by pointers (new per node,
// allocated depth-first), so every descent chases pointers.
#include <bits/stdc++.h>
using namespace std;

struct Node {
    long long sum = 0;
    Node *l = nullptr, *r = nullptr;
};

static vector<long long> a;

static Node *build(int lo, int hi) {
    Node *v = new Node;
    if (lo == hi) { v->sum = a[lo]; return v; }
    int mid = (lo + hi) / 2;
    v->l = build(lo, mid);
    v->r = build(mid + 1, hi);
    v->sum = v->l->sum + v->r->sum;
    return v;
}
static void update(Node *v, int lo, int hi, int pos, long long val) {
    while (true) {
        v->sum += val;
        if (lo == hi) return;
        int mid = (lo + hi) / 2;
        if (pos <= mid) { v = v->l; hi = mid; } else { v = v->r; lo = mid + 1; }
    }
}
static long long query(Node *v, int lo, int hi, int ql, int qr) {
    if (qr < lo || hi < ql) return 0;
    if (ql <= lo && hi <= qr) return v->sum;
    int mid = (lo + hi) / 2;
    return query(v->l, lo, mid, ql, qr) + query(v->r, mid + 1, hi, ql, qr);
}

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int n, q;
    cin >> n >> q;
    a.resize(n + 1);
    for (int i = 1; i <= n; ++i) cin >> a[i];
    Node *root = build(1, n);
    string out;
    for (int k = 0; k < q; ++k) {
        int t, x;
        long long y;
        cin >> t >> x >> y;
        if (t == 1) update(root, 1, n, x, y);
        else { out += to_string(query(root, 1, n, x, (int)y)); out += '\n'; }
    }
    cout << out;
}

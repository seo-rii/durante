// Range-add / range-sum Fenwick template (two BITs) used for point updates:
// every update and query touches both trees.
#include <bits/stdc++.h>
using namespace std;
static inline long long readInt() {
    int c = getchar_unlocked();
    while (c < '0' || c > '9') c = getchar_unlocked();
    long long x = 0;
    while (c >= '0' && c <= '9') { x = x * 10 + (c - '0'); c = getchar_unlocked(); }
    return x;
}
struct RangeBIT {
    int n;
    vector<long long> b1, b2;
    RangeBIT(int n) : n(n), b1(n + 2, 0), b2(n + 2, 0) {}
    void add(vector<long long> &b, int i, long long v) { for (; i <= n; i += i & -i) b[i] += v; }
    void rangeAdd(int l, int r, long long v) {
        add(b1, l, v); add(b1, r + 1, -v);
        add(b2, l, v * (l - 1)); add(b2, r + 1, -v * r);
    }
    long long sum(vector<long long> &b, int i) { long long s = 0; for (; i > 0; i -= i & -i) s += b[i]; return s; }
    long long prefix(int i) { return sum(b1, i) * i - sum(b2, i); }
    long long rangeSum(int l, int r) { return prefix(r) - prefix(l - 1); }
};
int main() {
    int n = readInt(), q = readInt();
    RangeBIT bit(n);
    for (int i = 1; i <= n; ++i) bit.rangeAdd(i, i, readInt());
    string out;
    out.reserve(1 << 23);
    for (int k = 0; k < q; ++k) {
        int t = readInt(), x = readInt();
        long long y = readInt();
        if (t == 1) bit.rangeAdd(x, x, y);
        else { out += to_string(bit.rangeSum(x, (int)y)); out += '\n'; }
    }
    fputs(out.c_str(), stdout);
}

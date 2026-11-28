// Square-root decomposition: block sums plus raw values; a query sums the
// partial blocks element by element and the whole blocks in between.
#include <bits/stdc++.h>
using namespace std;

static inline long long readInt() {
    int c = getchar_unlocked();
    while (c < '0' || c > '9') c = getchar_unlocked();
    long long x = 0;
    while (c >= '0' && c <= '9') { x = x * 10 + (c - '0'); c = getchar_unlocked(); }
    return x;
}

int main() {
    int n = readInt(), q = readInt();
    int B = max(1, (int)sqrt((double)n));
    vector<long long> a(n), blk(n / B + 1, 0);
    for (int i = 0; i < n; ++i) { a[i] = readInt(); blk[i / B] += a[i]; }
    string out;
    out.reserve(1 << 23);
    for (int k = 0; k < q; ++k) {
        int type = readInt(), x = readInt() - 1;
        long long y = readInt();
        if (type == 1) {
            a[x] += y;
            blk[x / B] += y;
        } else {
            int l = x, r = (int)y - 1;
            long long s = 0;
            int bl = l / B, br = r / B;
            if (bl == br) {
                for (int i = l; i <= r; ++i) s += a[i];
            } else {
                for (int i = l; i < (bl + 1) * B; ++i) s += a[i];
                for (int b = bl + 1; b < br; ++b) s += blk[b];
                for (int i = br * B; i <= r; ++i) s += a[i];
            }
            out += to_string(s);
            out += '\n';
        }
    }
    fputs(out.c_str(), stdout);
}

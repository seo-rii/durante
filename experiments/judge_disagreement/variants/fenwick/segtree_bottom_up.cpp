// Iterative bottom-up segment tree (2n array) with an fread-based reader and a
// hand-rolled integer writer.
#include <bits/stdc++.h>
using namespace std;

static char ibuf[1 << 16];
static size_t ilen = 0, ipos = 0;
static inline int gc() {
    if (ipos == ilen) { ilen = fread(ibuf, 1, sizeof ibuf, stdin); ipos = 0; if (!ilen) return -1; }
    return ibuf[ipos++];
}
static inline long long readInt() {
    int c = gc();
    while (c < '0' || c > '9') c = gc();
    long long x = 0;
    while (c >= '0' && c <= '9') { x = x * 10 + (c - '0'); c = gc(); }
    return x;
}

int main() {
    int n = readInt(), q = readInt();
    vector<long long> t(2 * n);
    for (int i = 0; i < n; ++i) t[n + i] = readInt();
    for (int i = n - 1; i > 0; --i) t[i] = t[2 * i] + t[2 * i + 1];
    string out;
    out.reserve(1 << 23);
    char tmp[24];
    for (int k = 0; k < q; ++k) {
        int type = readInt(), x = readInt();
        long long y = readInt();
        if (type == 1) {
            for (int i = x - 1 + n; i >= 1; i >>= 1) t[i] += y;
        } else {
            long long s = 0;
            for (int l = x - 1 + n, r = (int)y + n; l < r; l >>= 1, r >>= 1) {
                if (l & 1) s += t[l++];
                if (r & 1) s += t[--r];
            }
            int p = 0;
            do { tmp[p++] = '0' + s % 10; s /= 10; } while (s);
            while (p) out += tmp[--p];
            out += '\n';
        }
    }
    fwrite(out.data(), 1, out.size(), stdout);
}

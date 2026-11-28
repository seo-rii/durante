// Rabin-Karp with a 64-bit polynomial hash modulo 2^61-1 (128-bit multiplies);
// every hash hit is confirmed with memcmp, so the answer is exact.
#include <bits/stdc++.h>
using namespace std;
typedef unsigned long long u64;
static const u64 MODP = (1ULL << 61) - 1;

static inline u64 mulmod(u64 a, u64 b) {
    __uint128_t r = (__uint128_t)a * b;
    u64 lo = (u64)(r & MODP), hi = (u64)(r >> 61);
    u64 s = lo + hi;
    return s >= MODP ? s - MODP : s;
}

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    string t, p;
    cin >> t >> p;
    int n = t.size(), m = p.size();
    long long count = 0, total = 0;
    if (m <= n) {
        const u64 B = 911382323;
        u64 hp = 0, ht = 0, pw = 1;
        for (int i = 0; i < m; ++i) {
            hp = (mulmod(hp, B) + (unsigned char)p[i]) % MODP;
            ht = (mulmod(ht, B) + (unsigned char)t[i]) % MODP;
            pw = mulmod(pw, B);
        }
        for (int i = 0;; ++i) {
            if (ht == hp && memcmp(t.data() + i, p.data(), m) == 0) { ++count; total += i + 1; }
            if (i + m >= n) break;
            ht = mulmod(ht, B) + (unsigned char)t[i + m];
            u64 sub = mulmod(pw, (unsigned char)t[i]);
            ht = (ht >= MODP ? ht - MODP : ht);
            ht = ht >= sub ? ht - sub : ht + MODP - sub;
        }
    }
    cout << count << ' ' << total << '\n';
}

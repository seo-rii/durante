// Shift-And (bitap): the pattern (<= 64 chars) is a bit mask per byte value;
// one shift, OR and AND per text character, branch-free inner update.
#include <bits/stdc++.h>
using namespace std;
typedef unsigned long long u64;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    string t, p;
    cin >> t >> p;
    int m = p.size();
    long long count = 0, total = 0;
    if (m <= 64) {
        u64 mask[256] = {0};
        for (int k = 0; k < m; ++k) mask[(unsigned char)p[k]] |= 1ULL << k;
        const u64 hit = 1ULL << (m - 1);
        u64 d = 0;
        for (int i = 0, n = t.size(); i < n; ++i) {
            d = ((d << 1) | 1) & mask[(unsigned char)t[i]];
            if (d & hit) { ++count; total += i - m + 2; }
        }
    } else {  // long patterns: plain KMP
        vector<int> fail(m, 0);
        for (int i = 1, k = 0; i < m; ++i) {
            while (k > 0 && p[i] != p[k]) k = fail[k - 1];
            if (p[i] == p[k]) ++k;
            fail[i] = k;
        }
        for (int i = 0, k = 0; i < (int)t.size(); ++i) {
            while (k > 0 && t[i] != p[k]) k = fail[k - 1];
            if (t[i] == p[k]) ++k;
            if (k == m) { ++count; total += i - m + 2; k = fail[k - 1]; }
        }
    }
    cout << count << ' ' << total << '\n';
}

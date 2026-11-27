#include <cstdio>
#include <vector>
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
    vector<long long> bit(n + 1, 0);
    for (int i = 1; i <= n; ++i) {
        bit[i] += readInt();
        int j = i + (i & -i);
        if (j <= n) bit[j] += bit[i];
    }
    auto prefix = [&](int i) { long long s = 0; for (; i > 0; i -= i & -i) s += bit[i]; return s; };
    vector<char> out;
    out.reserve(1 << 22);
    char tmp[24];
    for (int k = 0; k < q; ++k) {
        int t = readInt(), x = readInt();
        long long y = readInt();
        if (t == 1) {
            for (int i = x; i <= n; i += i & -i) bit[i] += y;
        } else {
            long long s = prefix((int)y) - prefix(x - 1);
            int l = snprintf(tmp, sizeof tmp, "%lld\n", s);
            out.insert(out.end(), tmp, tmp + l);
        }
    }
    fwrite(out.data(), 1, out.size(), stdout);
}

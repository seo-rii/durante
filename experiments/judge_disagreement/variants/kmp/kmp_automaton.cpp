// KMP compiled into a DFA over the bytes of the alphabet: one table lookup per
// text character, no failure-link while loop; text read with fread.
#include <bits/stdc++.h>
using namespace std;

int main() {
    size_t cap = 1 << 20, len = 0;
    vector<char> in(cap);
    for (size_t r; (r = fread(in.data() + len, 1, cap - len, stdin)) > 0;) {
        len += r;
        if (len == cap) { cap *= 2; in.resize(cap); }
    }
    size_t p = 0;
    while (p < len && isspace((unsigned char)in[p])) ++p;
    size_t ts = p;
    while (p < len && !isspace((unsigned char)in[p])) ++p;
    size_t te = p;
    while (p < len && isspace((unsigned char)in[p])) ++p;
    size_t ps = p;
    while (p < len && !isspace((unsigned char)in[p])) ++p;
    string pat(in.begin() + ps, in.begin() + p);
    int m = pat.size();
    // dfa[state][c]: next state after reading byte c in state (state m = full match).
    vector<array<int, 256>> dfa(m + 1);
    for (auto &row : dfa) row.fill(0);
    dfa[0][(unsigned char)pat[0]] = 1;
    for (int s = 1, x = 0; s <= m; ++s) {
        dfa[s] = dfa[x];
        if (s < m) { dfa[s][(unsigned char)pat[s]] = s + 1; x = dfa[x][(unsigned char)pat[s]]; }
    }
    long long count = 0, total = 0;
    int s = 0;
    for (size_t i = ts; i < te; ++i) {
        s = dfa[s][(unsigned char)in[i]];
        if (s == m) { ++count; total += (long long)(i - ts) - m + 2; }
    }
    printf("%lld %lld\n", count, total);
}

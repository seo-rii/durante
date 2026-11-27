#include <bits/stdc++.h>
using namespace std;

int main() {
    int n, q;
    if (scanf("%d %d", &n, &q) != 2) return 1;
    vector<int> a(n);
    for (auto &x : a) if (scanf("%d", &x) != 1) return 1;
    sort(a.begin(), a.end());
    string out;
    out.reserve((size_t)q * 8);
    for (int i = 0; i < q; ++i) {
        int x;
        if (scanf("%d", &x) != 1) return 1;
        out += to_string(upper_bound(a.begin(), a.end(), x) - a.begin());
        out += '\n';
    }
    fputs(out.c_str(), stdout);
}

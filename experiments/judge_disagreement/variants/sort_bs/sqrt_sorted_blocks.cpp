// Square-root decomposition template (sorted blocks of size sqrt(n log n)):
// every query binary-searches each of the ~160 blocks, O(q sqrt(n log n)).
#include <bits/stdc++.h>
using namespace std;

int main() {
    int n, q;
    if (scanf("%d %d", &n, &q) != 2) return 1;
    vector<int> a(n);
    for (auto &x : a) if (scanf("%d", &x) != 1) return 1;
    int B = max(1, (int)sqrt(n * log2(max(2, n))));
    vector<vector<int>> blocks;
    for (int i = 0; i < n; i += B) {
        blocks.emplace_back(a.begin() + i, a.begin() + min(n, i + B));
        sort(blocks.back().begin(), blocks.back().end());
    }
    string out;
    for (int i = 0; i < q; ++i) {
        int x;
        if (scanf("%d", &x) != 1) return 1;
        long long cnt = 0;
        for (auto &b : blocks) cnt += upper_bound(b.begin(), b.end(), x) - b.begin();
        out += to_string(cnt);
        out += '\n';
    }
    fputs(out.c_str(), stdout);
}

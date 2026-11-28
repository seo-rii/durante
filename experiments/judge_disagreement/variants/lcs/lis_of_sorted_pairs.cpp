// LCS reduced to LIS: materialise every matching (i, j) pair (~n*m/4 of them,
// 128 MB), std::sort them by (i asc, j desc), then a patience-sorting LIS on j.
#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    string a, b;
    cin >> a >> b;
    vector<pair<int, int>> pairs;
    for (int i = 0; i < (int)a.size(); ++i)
        for (int j = 0; j < (int)b.size(); ++j)
            if (a[i] == b[j]) pairs.push_back({i, j});
    sort(pairs.begin(), pairs.end(), [](const pair<int, int> &x, const pair<int, int> &y) {
        return x.first != y.first ? x.first < y.first : x.second > y.second;
    });
    vector<int> tails;
    for (auto &[i, j] : pairs) {
        auto it = lower_bound(tails.begin(), tails.end(), j);
        if (it == tails.end()) tails.push_back(j);
        else *it = j;
    }
    cout << tails.size() << '\n';
}

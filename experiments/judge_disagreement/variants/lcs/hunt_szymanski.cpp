// Hunt-Szymanski: LCS as a longest increasing subsequence over all matching
// (i, j) pairs, one binary search per pair (O(r log n), r ~ n*m/4 here).
#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    string a, b;
    cin >> a >> b;
    vector<int> pos[256];
    for (int j = 0; j < (int)b.size(); ++j) pos[(unsigned char)b[j]].push_back(j);
    vector<int> tails;  // tails[k] = smallest end position in b of a common subsequence of length k+1
    for (char ca : a) {
        const vector<int> &p = pos[(unsigned char)ca];
        for (int k = (int)p.size() - 1; k >= 0; --k) {
            int j = p[k];
            auto it = lower_bound(tails.begin(), tails.end(), j);
            if (it == tails.end()) tails.push_back(j);
            else *it = j;
        }
    }
    cout << tails.size() << '\n';
}

// Same sort + upper_bound, but iostream (sync off) with endl after every
// answer: one flush, i.e. one write(2) system call, per query.
#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int n, q;
    cin >> n >> q;
    vector<int> a(n);
    for (auto &x : a) cin >> x;
    sort(a.begin(), a.end());
    for (int i = 0; i < q; ++i) {
        int x;
        cin >> x;
        cout << upper_bound(a.begin(), a.end(), x) - a.begin() << endl;
    }
}

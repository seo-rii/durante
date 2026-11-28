// Same sort + upper_bound with cin/cout left synchronised with stdio
// ('\n', no endl): the cost moves into the iostream/stdio layering.
#include <bits/stdc++.h>
using namespace std;

int main() {
    int n, q;
    cin >> n >> q;
    vector<int> a(n);
    for (auto &x : a) cin >> x;
    sort(a.begin(), a.end());
    for (int i = 0; i < q; ++i) {
        int x;
        cin >> x;
        cout << upper_bound(a.begin(), a.end(), x) - a.begin() << '\n';
    }
}

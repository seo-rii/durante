#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    string t, p;
    cin >> t >> p;
    int m = p.size();
    vector<int> fail(m, 0);
    for (int i = 1, k = 0; i < m; ++i) {
        while (k > 0 && p[i] != p[k]) k = fail[k - 1];
        if (p[i] == p[k]) ++k;
        fail[i] = k;
    }
    long long count = 0, total = 0;
    for (int i = 0, k = 0; i < (int)t.size(); ++i) {
        while (k > 0 && t[i] != p[k]) k = fail[k - 1];
        if (t[i] == p[k]) ++k;
        if (k == m) { ++count; total += i - m + 2; k = fail[k - 1]; }
    }
    cout << count << ' ' << total << '\n';
}

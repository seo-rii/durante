// Values collected in a std::list and sorted with list::sort (pointer-chasing
// merge sort over scattered nodes), then copied to a vector for upper_bound.
#include <bits/stdc++.h>
using namespace std;

int main() {
    int n, q;
    if (scanf("%d %d", &n, &q) != 2) return 1;
    list<int> l;
    for (int i = 0; i < n; ++i) {
        int x;
        if (scanf("%d", &x) != 1) return 1;
        l.push_back(x);
    }
    l.sort();
    vector<int> a(l.begin(), l.end());
    for (int i = 0; i < q; ++i) {
        int x;
        if (scanf("%d", &x) != 1) return 1;
        printf("%d\n", (int)(upper_bound(a.begin(), a.end(), x) - a.begin()));
    }
}

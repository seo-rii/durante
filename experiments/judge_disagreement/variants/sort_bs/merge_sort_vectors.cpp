// Hand-written recursive merge sort that copies each half into fresh vectors
// at every level (allocation heavy), then a hand-written binary search;
// scanf/printf I/O.
#include <bits/stdc++.h>
using namespace std;

static void mergeSort(vector<int> &v) {
    if (v.size() < 2) return;
    size_t mid = v.size() / 2;
    vector<int> left(v.begin(), v.begin() + mid), right(v.begin() + mid, v.end());
    mergeSort(left);
    mergeSort(right);
    size_t i = 0, j = 0, k = 0;
    while (i < left.size() && j < right.size()) v[k++] = left[i] <= right[j] ? left[i++] : right[j++];
    while (i < left.size()) v[k++] = left[i++];
    while (j < right.size()) v[k++] = right[j++];
}

int main() {
    int n, q;
    if (scanf("%d %d", &n, &q) != 2) return 1;
    vector<int> a(n);
    for (auto &x : a) if (scanf("%d", &x) != 1) return 1;
    mergeSort(a);
    for (int i = 0; i < q; ++i) {
        int x;
        if (scanf("%d", &x) != 1) return 1;
        int lo = 0, hi = n;  // first index with a[idx] > x
        while (lo < hi) {
            int mid = (lo + hi) / 2;
            if (a[mid] <= x) lo = mid + 1; else hi = mid;
        }
        printf("%d\n", lo);
    }
}

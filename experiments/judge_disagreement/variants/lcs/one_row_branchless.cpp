// Single DP row updated in place with a saved diagonal and branch-free max,
// scanf input into char buffers.
#include <bits/stdc++.h>
using namespace std;

static char A[1 << 20], B[1 << 20];

int main() {
    if (scanf("%s %s", A, B) != 2) return 1;
    int n = strlen(A), m = strlen(B);
    vector<int> dp(m + 1, 0);
    for (int i = 0; i < n; ++i) {
        int diag = 0, left = 0;
        char ca = A[i];
        for (int j = 1; j <= m; ++j) {
            int up = dp[j];
            int match = diag + 1;
            int best = up > left ? up : left;
            int v = ca == B[j - 1] ? match : best;
            diag = up;
            dp[j] = left = v;
        }
    }
    printf("%d\n", dp[m]);
}

// IOI 2014 Wall, with the sample grader's stdin and stdout format.
#include <algorithm>
#include <cstdio>
#include <vector>

constexpr int max_height = 100000;

struct Clamp {
    int low = 0;
    int high = max_height;

    // The newer clamp is applied after this node's pending operations.
    void apply(Clamp newer) {
        low = std::clamp(low, newer.low, newer.high);
        high = std::clamp(high, newer.low, newer.high);
    }
};

class Wall {
    std::vector<Clamp> pending;

    void push(int node) {
        pending[node * 2].apply(pending[node]);
        pending[node * 2 + 1].apply(pending[node]);
        pending[node] = {};
    }

public:
    explicit Wall(int size) : pending(4 * size) {}

    void update(int node, int start, int end, int left, int right, Clamp operation) {
        if (left <= start && end <= right) {
            pending[node].apply(operation);
            return;
        }
        push(node);
        int middle = (start + end) / 2;
        if (left <= middle) update(node * 2, start, middle, left, right, operation);
        if (middle < right) update(node * 2 + 1, middle + 1, end, left, right, operation);
    }

    void print(int node, int start, int end) {
        if (start == end) {
            // Applying the pending clamp to the initial zero height gives its lower bound.
            std::printf("%d\n", pending[node].low);
            return;
        }
        push(node);
        int middle = (start + end) / 2;
        print(node * 2, start, middle);
        print(node * 2 + 1, middle + 1, end);
    }
};

int main() {
    int n, k;
    if (std::scanf("%d%d", &n, &k) != 2 || n < 1 || n > 2000000 || k < 1 || k > 500000)
        return 1;
    Wall wall(n);
    for (int i = 0; i < k; ++i) {
        int op, left, right, height;
        if (std::scanf("%d%d%d%d", &op, &left, &right, &height) != 4 ||
            (op != 1 && op != 2) || left < 0 || right < left || right >= n ||
            height < 0 || height > max_height)
            return 1;
        Clamp operation = op == 1 ? Clamp{height, max_height} : Clamp{0, height};
        wall.update(1, 0, n - 1, left, right, operation);
    }
    wall.print(1, 0, n - 1);
}

#include <cstdint>
#include <cstdio>
#include <vector>

int main() {
    unsigned size, operations;
    if (std::scanf("%u%u", &size, &operations) != 2 || size == 0 || size > (1U << 22))
        return 1;
    std::vector<std::uint32_t> tree(size + 1);
    std::uint32_t state = 12345;
    std::uint64_t total = 0;
    for (unsigned step = 0; step < operations; ++step) {
        state = state * 1664525U + 1013904223U;
        unsigned index = 1 + (state >> 5) % size;
        if (step % 2 == 0) {
            for (; index <= size; index += index & -index)
                tree[index] += state;
        } else {
            for (; index; index -= index & -index)
                total += tree[index];
        }
    }
    std::printf("%llu\n", static_cast<unsigned long long>(total));
}

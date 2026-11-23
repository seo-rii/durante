// IOI 2012 Crayfish Scrivener, using binary lifting on the tree of typed letters.
#include <bit>
#include <iostream>
#include <vector>

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    int operations;
    if (!(std::cin >> operations) || operations < 0) return 1;
    const std::size_t levels = std::bit_width(static_cast<unsigned>(operations));

    // For node v, row v contains its ancestors at distances 1, 2, 4, ...
    std::vector<int> ancestors(levels, 0);
    ancestors.reserve((static_cast<std::size_t>(operations) + 1) * levels);
    std::vector<int> depth{0}, history{0};
    std::vector<char> letters{'\0'};
    for (int i = 0; i < operations; ++i) {
        char command;
        if (!(std::cin >> command)) return 1;
        int node = history.back();
        if (command == 'T') {
            char letter;
            if (!(std::cin >> letter)) return 1;
            history.push_back(static_cast<int>(depth.size()));
            depth.push_back(depth[node] + 1);
            letters.push_back(letter);
            ancestors.push_back(node);
            for (std::size_t level = 1; level < levels; ++level) {
                node = ancestors[node * levels + level - 1];
                ancestors.push_back(node);
            }
        } else if (command == 'U') {
            int undo;
            if (!(std::cin >> undo) || undo < 0 || static_cast<std::size_t>(undo) >= history.size())
                return 1;
            history.push_back(history[history.size() - 1 - undo]);
        } else if (command == 'P') {
            int position;
            if (!(std::cin >> position) || position < 0 || position >= depth[node]) return 1;
            unsigned distance = depth[node] - 1 - position;
            while (distance) {
                int level = std::countr_zero(distance);
                node = ancestors[node * levels + level];
                distance &= distance - 1;
            }
            std::cout << letters[node] << '\n';
        } else {
            return 1;
        }
    }
}

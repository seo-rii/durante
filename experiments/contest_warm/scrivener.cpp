// IOI 2012 Crayfish Scrivener. The driver follows the sample grader's text format.
#include <iostream>
#include <vector>

struct Letter {
    int parent;
    int jump;
    int depth;
    char value;
};

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    int operations;
    if (!(std::cin >> operations)) return 1;

    std::vector<Letter> nodes{{0, 0, 0, 0}};
    std::vector<int> history{0};
    nodes.reserve(operations + 1);
    history.reserve(operations + 1);
    for (int i = 0; i < operations; ++i) {
        char command;
        std::cin >> command;
        int node = history.back();
        if (command == 'T') {
            char value;
            std::cin >> value;
            int first = nodes[node].jump;
            int second = nodes[first].jump;
            // Merge two equal length jumps into a longer jump from the new node.
            int jump = node;
            if (nodes[node].depth - nodes[first].depth == nodes[first].depth - nodes[second].depth)
                jump = second;
            history.push_back(static_cast<int>(nodes.size()));
            nodes.push_back({node, jump, nodes[node].depth + 1, value});
        } else if (command == 'U') {
            int undo;
            std::cin >> undo;
            history.push_back(history[history.size() - 1 - undo]);
        } else if (command == 'P') {
            int position;
            std::cin >> position;
            int depth = position + 1;
            while (nodes[node].depth > depth) {
                int jump = nodes[node].jump;
                node = nodes[jump].depth >= depth ? jump : nodes[node].parent;
            }
            std::cout << nodes[node].value << '\n';
        } else {
            return 1;
        }
    }
}

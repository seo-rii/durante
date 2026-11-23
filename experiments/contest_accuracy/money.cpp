// ICPC World Finals 2017, Money for Nothing. Each offer is (price, day).
#include <algorithm>
#include <cstdint>
#include <iostream>
#include <utility>
#include <vector>

using Offer = std::pair<int, int>;

std::vector<Offer> read_offers(int count) {
    std::vector<Offer> offers(count);
    for (auto& [price, day] : offers) std::cin >> price >> day;
    std::sort(offers.begin(), offers.end());
    return offers;
}

struct Row {
    Offer producer;
    int first_consumer;
    int past_consumer;
};

// On each skyline, price increases and day decreases. Moving to a later
// producer makes a later consumer relatively more profitable: the difference
// of two consumer profits increases with producer price and decreasing day.
// The feasible interval also moves right, so leftmost maximizers are monotone.
std::int64_t best_profit(const std::vector<Row>& rows, const std::vector<Offer>& consumers,
                         int begin, int end, int left, int right) {
    if (begin == end) return 0;
    const int mid = begin + (end - begin) / 2;
    const Row& row = rows[mid];
    const auto [price, day] = row.producer;
    int best = std::max(left, row.first_consumer);
    std::int64_t profit = 0;
    for (int j = best; j < std::min(right, row.past_consumer); ++j) {
        const auto [bid, until] = consumers[j];
        const auto value = std::int64_t(bid - price) * (until - day);
        if (value > profit) {
            profit = value;
            best = j;
        }
    }
    return std::max({profit, best_profit(rows, consumers, begin, mid, left, best + 1),
                     best_profit(rows, consumers, mid + 1, end, best, right)});
}

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    int m, n;
    if (!(std::cin >> m >> n)) return 1;
    const auto supply = read_offers(m);
    const auto demand = read_offers(n);

    // A higher bid ending on the same or a later day dominates a lower one.
    std::vector<Offer> consumers;
    for (auto it = demand.rbegin(); it != demand.rend(); ++it) {
        if (consumers.empty() || it->second > consumers.back().second) consumers.push_back(*it);
    }
    std::reverse(consumers.begin(), consumers.end());

    std::vector<Row> rows;
    int earliest = 1'000'000'001;
    int first = 0, past = 0;
    const int count = static_cast<int>(consumers.size());
    for (const auto& producer : supply) {
        auto [price, day] = producer;
        if (day >= earliest) continue;
        earliest = day;
        while (first < count && consumers[first].first <= price) ++first;
        while (past < count && consumers[past].second > day) ++past;
        if (first < past) rows.push_back({producer, first, past});
    }
    std::cout << best_profit(rows, consumers, 0, static_cast<int>(rows.size()), 0, count) << '\n';
}

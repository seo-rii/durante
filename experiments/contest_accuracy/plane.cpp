// ICPC World Finals 2018, Catch the Plane.
#include <algorithm>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <vector>

struct Bus {
    int origin;
    int destination;
    double probability;
    double at_arrival;
};

struct Event {
    std::int64_t time;
    int bus;
    bool departure;
};

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    int routes, stations;
    std::int64_t deadline;
    if (!(std::cin >> routes >> stations >> deadline)) {
        return 1;
    }

    std::vector<Bus> buses(routes);
    std::vector<Event> events;
    events.reserve(2 * routes);
    for (int i = 0; i < routes; ++i) {
        std::int64_t departure, arrival;
        auto& bus = buses[i];
        std::cin >> bus.origin >> bus.destination >> departure >> arrival >> bus.probability;
        events.push_back({departure, i, true});
        events.push_back({arrival, i, false});
    }
    std::sort(events.begin(), events.end(), [](const Event& a, const Event& b) {
        return a.time > b.time;
    });

    std::vector<double> best(stations, 0.0), pending(stations, 0.0);
    best[1] = 1.0;
    for (std::size_t first = 0; first < events.size();) {
        std::size_t end = first;
        while (end < events.size() && events[end].time == events[first].time) {
            ++end;
        }
        // Read all states strictly after this time before publishing any departure.
        // An arrival cannot catch an equal-time bus. A failed trip cannot try a
        // second simultaneous departure either.
        for (std::size_t i = first; i < end; ++i) {
            const Event& e = events[i];
            Bus& bus = buses[e.bus];
            if (e.departure) {
                double chance = bus.probability * bus.at_arrival + (1.0 - bus.probability) * best[bus.origin];
                pending[bus.origin] = std::max(pending[bus.origin], chance);
            } else {
                bus.at_arrival = best[bus.destination];
            }
        }
        for (std::size_t i = first; i < end; ++i) {
            const Event& e = events[i];
            if (e.departure) {
                const Bus& bus = buses[e.bus];
                best[bus.origin] = std::max(best[bus.origin], pending[bus.origin]);
            }
        }
        first = end;
    }
    std::cout << std::fixed << std::setprecision(9) << best[0] << '\n';
}

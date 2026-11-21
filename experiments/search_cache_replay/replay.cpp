// Post-hoc replay of reads from the key array of the archived Rust lower_bound.
// All caches start empty. Each receives the same complete sequence of line IDs.
#include <algorithm>
#include <array>
#include <charconv>
#include <cstdint>
#include <iostream>
#include <limits>
#include <list>
#include <stdexcept>
#include <string_view>
#include <vector>

constexpr uint32_t line_bytes = 64;
constexpr uint32_t element_bytes = 4;

class EightWay {
    using Set = std::array<uint32_t, 8>;
    std::vector<Set> sets_;

public:
    explicit EightWay(uint32_t set_count) : sets_(set_count) {
        for (Set &set : sets_) {
            set.fill(std::numeric_limits<uint32_t>::max());
        }
    }

    bool miss(uint32_t line) {
        // The 32 KiB and 512 KiB configurations have power-of-two set counts.
        Set &set = sets_[line & (sets_.size() - 1)];
        const auto found = std::find(set.begin(), set.end(), line);
        const bool missed = found == set.end();
        const auto slot = missed ? set.end() - 1 : found;
        std::move_backward(set.begin(), slot, slot + 1);
        set.front() = line;
        return missed;
    }
};

class FullyAssociative {
    // The lookup vector has one entry per possible line in the key array. The
    // LRU list contains only resident lines and has exactly the modeled capacity.
    std::list<uint32_t> lru_;
    std::vector<std::list<uint32_t>::iterator> by_line_;
    size_t capacity_;

public:
    FullyAssociative(size_t capacity, uint32_t addressable_lines)
        : by_line_(addressable_lines, lru_.end()), capacity_(capacity) {}

    bool miss(uint32_t line) {
        auto &where = by_line_[line];
        if (where != lru_.end()) {
            lru_.splice(lru_.begin(), lru_, where);
            return false;
        }
        if (lru_.size() == capacity_) {
            by_line_[lru_.back()] = lru_.end();
            lru_.pop_back();
        }
        lru_.push_front(line);
        where = lru_.begin();
        return true;
    }
};

static void require(bool value, const char *message) {
    if (!value) {
        throw std::runtime_error(message);
    }
}

static void self_test() {
    EightWay cache(2);
    for (uint32_t line = 0; line < 16; line += 2) {
        require(cache.miss(line), "cold admission");
    }
    require(!cache.miss(0), "promotion on hit");
    require(cache.miss(1), "second set cold admission");
    require(cache.miss(16), "overflow first set");
    require(!cache.miss(0) && !cache.miss(1), "recent line and independent set survive");
    require(cache.miss(2), "least recently used line evicted");

    FullyAssociative full(2, 6);
    require(full.miss(0) && full.miss(1), "fully associative cold lines");
    require(!full.miss(0) && full.miss(2), "fully associative hit promotes");
    require(!full.miss(0) && full.miss(1), "fully associative evicts oldest");
}

static uint32_t parse(const char *arg) {
    const std::string_view text(arg);
    uint32_t value = 0;
    auto result = std::from_chars(text.data(), text.data() + text.size(), value);
    require(result.ec == std::errc() && result.ptr == text.data() + text.size(),
            "invalid unsigned integer");
    return value;
}

template<bool full_shadow>
static void replay(uint32_t keys, uint32_t queries, uint32_t seed, uint32_t offset_bytes) {
    EightWay l1(64), l2(1024);
    FullyAssociative full(512, full_shadow ? 1 + (element_bytes * keys + offset_bytes) / line_bytes : 0);
    uint64_t loads = 0, l1_misses = 0, l2_misses = 0, full_misses = 0, checksum = 0;
    auto read_key = [&](uint32_t element) {
        const uint32_t line = (element_bytes * element + offset_bytes) / line_bytes;
        ++loads;
        l1_misses += l1.miss(line);
        l2_misses += l2.miss(line);
        if constexpr (full_shadow) {
            full_misses += full.miss(line);
        }
        return 2 * element;
    };

    for (uint32_t j = 0; j < queries; ++j) {
        seed ^= seed << 13;
        seed ^= seed >> 17;
        seed ^= seed << 5;
        const uint32_t target = seed % (2 * keys + 1);
        uint32_t base = 0, size = keys;
        while (size > 1) {
            const uint32_t half = size / 2, candidate = base + half;
            if (read_key(candidate) < target) {
                base = candidate;
            }
            size -= half;
        }
        checksum += base + (read_key(base) < target);
    }
    std::cout << loads << ' ' << checksum << ' ' << l1_misses << ' ' << l2_misses;
    if constexpr (full_shadow) {
        std::cout << ' ' << full_misses;
    }
    std::cout << '\n';
}

int main(int argc, char **argv) {
    try {
        if (argc == 2 && std::string_view(argv[1]) == "--self-test") {
            self_test();
            return 0;
        }
        require(argc == 5, "usage: replay KEYS QUERIES SEED OFFSET_BYTES");
        const uint32_t keys = parse(argv[1]), queries = parse(argv[2]);
        const uint32_t seed = parse(argv[3]), offset = parse(argv[4]);
        require(keys > 0 && keys <= (1u << 21) && queries > 0 && seed != 0,
                "invalid study workload dimensions");
        require(offset < line_bytes && offset % element_bytes == 0, "invalid u32 alignment");
        if (offset == 0) {
            replay<true>(keys, queries, seed, offset);
        } else {
            replay<false>(keys, queries, seed, offset);
        }
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

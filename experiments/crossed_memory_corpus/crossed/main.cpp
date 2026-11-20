#include <cinttypes>
#include <cstdio>

constexpr uint32_t max_entries = 1u << 22;
constexpr uint32_t chunk = 8192;
alignas(64) static uint32_t table[max_entries];
alignas(64) static unsigned char source[chunk], destination[chunk];

extern "C" __attribute__((noinline)) uint64_t gather(uint32_t mask) {
    uint32_t state = 193177;
    uint64_t sum = 0;
    for (uint32_t i = 0; i < (1u << 23); ++i) {
        state ^= state << 13;
        state ^= state >> 17;
        state ^= state << 5;
        sum += table[state & mask];
    }
    return sum;
}

int main() {
    uint32_t bytes, entries;
    if (std::scanf("%" SCNu32 " %" SCNu32, &bytes, &entries) != 2 ||
        (bytes != 0 && bytes != chunk) ||
        (entries != 1024 && entries != 262144 && entries != max_entries))
        return 1;
    for (uint32_t i = 0; i < max_entries; ++i)
        table[i] = 2654435761u * i + 12345u;
    for (uint32_t i = 0; i < chunk; ++i) {
        source[i] = static_cast<unsigned char>(29 * i + 7);
        destination[i] = static_cast<unsigned char>(37 * i + 11);
    }
    const uint64_t sum = gather(entries - 1);
    for (uint32_t i = 0; i < 32768; ++i) {
        auto *src = source;
        auto *dst = destination;
        size_t count = bytes;
        asm volatile("rep movsb" : "+S"(src), "+D"(dst), "+c"(count) : : "memory");
    }
    std::printf("%" PRIu64 " %u\n", sum, static_cast<unsigned>(destination[471]));
}

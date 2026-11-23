/* Immutable main-module offsets from the ELF-bound profile. Parse before
 * opening a report or registering any event. */
enum { MAX_PROFILE = 64 };
static uintptr_t offsets[MAX_PROFILE];
static unsigned profile_size;

static void parse_sites(const char *p) {
    while (true) {
        if (profile_size == MAX_PROFILE || *p < '0' || *p > '9' ||
            (*p == '0' && p[1] >= '0' && p[1] <= '9'))
            fail("invalid or oversized decimal offset list");
        uintptr_t value = 0;
        do {
            unsigned digit = (unsigned)(*p++ - '0');
            if (value > (UINTPTR_MAX - digit) / 10)
                fail("offset exceeds address width");
            value = value * 10 + digit;
        } while (*p >= '0' && *p <= '9');
        if (profile_size && offsets[profile_size - 1] >= value)
            fail("offsets must be strictly increasing");
        offsets[profile_size++] = value;
        if (*p == '\0')
            return;
        if (*p++ != ',')
            fail("invalid offset separator");
    }
}

static bool selected(uintptr_t offset) {
    if (offset < offsets[0] || offset > offsets[profile_size - 1])
        return false;
    if (profile_size == 1)
        return true;
    unsigned low = 0, high = profile_size;
    while (low < high) {
        unsigned mid = low + (high-low)/2;
        if (offsets[mid] < offset)
            low = mid + 1;
        else
            high = mid;
    }
    return offsets[low] == offset;
}

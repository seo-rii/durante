/* Canonical little-endian x86-64 wire format: an eight-byte magic and ten
 * uint64_t header fields, then six uint64_t per executed site, in PC order. */
static void write_all(const void *data, size_t size) {
    const byte *p = data;
    while (size) {
        ssize_t n = dr_write_file(output, p, size);
        if (n <= 0 || (size_t)n > size)
            fail("cannot write packed receipt");
        p += n;
        size -= (size_t)n;
    }
}

static void write_packed(void) {
    uint64_t loads = 0, pairs = 0, near = 0, count = 0;
    for (unsigned i = 0; i < used; ++i) {
        uint64_t n = visits(&sites[i]);
        if (!n) continue;
        if (UINT64_MAX - loads < n || sites[i].min_address > sites[i].max_address)
            fail("invalid counters or sampled address");
        ++count;
        loads += n;
        pairs += sites[i].pairs;
        near += sites[i].near;
    }
    uint64_t header[] = {0, 2, (unsigned)owner, 1, PERIOD, LINE, loads, pairs, near, pairs-near, count};
    memcpy(header, "DSPAN001", 8);
    write_all(header, sizeof(header));

    enum { CHUNK = 32, FIELDS = 6 };
    uint64_t rows[CHUNK][FIELDS];
    unsigned buffered = 0;
    for (unsigned i = 0; i < used; ++i) {
        site_t *s = &sites[i];
        uint64_t n = visits(s);
        if (!n) continue;
        uint64_t *row = rows[buffered++];
        row[0] = (uintptr_t)s->pc - (uintptr_t)module_start;
        row[1] = n;
        row[2] = s->pairs;
        row[3] = s->near;
        row[4] = s->pairs - s->near;
        row[5] = s->max_address - s->min_address;
        if (buffered == CHUNK) {
            write_all(rows, sizeof(rows));
            buffered = 0;
        }
    }
    if (buffered)
        write_all(rows, buffered * sizeof(rows[0]));
}

/* Entry profiler for the registered single-threaded static x86-64 executables. */
#include "dr_api.h"
#include <stdint.h>
#include <stdlib.h>

enum { NEW, MALLOC, FREE, CALLOC, REALLOC, KINDS };
static app_pc entry[KINDS];
static uint64_t calls[KINDS], outstanding, peak, underflows;
static uint64_t requested_bytes, sizes[129];

static void observe(unsigned kind, reg_t arg) {
    ++calls[kind];
    if (kind == NEW) {
        requested_bytes += arg;
        if (arg < sizeof(sizes) / sizeof(sizes[0]))
            ++sizes[arg];
    }
    if (kind == MALLOC || kind == CALLOC) {
        ++outstanding;
        if (peak < outstanding)
            peak = outstanding;
    } else if (kind == FREE && arg != 0) {
        if (outstanding != 0)
            --outstanding;
        else
            ++underflows;
    }
}

static dr_emit_flags_t instrument(void *ctx, void *tag, instrlist_t *block,
                                  bool for_trace, bool translating) {
    (void)for_trace;
    (void)translating;
    for (unsigned i = 0; i < KINDS; ++i) {
        if (tag == entry[i])
            dr_insert_clean_call(ctx, block, instrlist_first_app(block), (void *)observe,
                                 false, 2, OPND_CREATE_INT32(i), opnd_create_reg(DR_REG_XDI));
    }
    return DR_EMIT_DEFAULT;
}

static void done(void) {
    dr_fprintf(STDERR, "ENTRIES");
    for (unsigned i = 0; i < KINDS; ++i)
        dr_fprintf(STDERR, " %llu", (unsigned long long)calls[i]);
    dr_fprintf(STDERR, "\nBALANCE %llu %llu %llu\nREQUESTED %llu\n",
               (unsigned long long)outstanding, (unsigned long long)peak,
               (unsigned long long)underflows, (unsigned long long)requested_bytes);
    for (unsigned i = 0; i < sizeof(sizes) / sizeof(sizes[0]); ++i)
        if (sizes[i] != 0)
            dr_fprintf(STDERR, "SIZE %u %llu\n", i, (unsigned long long)sizes[i]);
}

DR_EXPORT void dr_client_main(client_id_t id, int argc, const char *argv[]) {
    (void)id;
    if (argc != KINDS + 1)
        dr_abort();
    for (unsigned i = 0; i < KINDS; ++i)
        entry[i] = (app_pc)(uintptr_t)strtoull(argv[i + 1], NULL, 16);
    dr_register_bb_event(instrument);
    dr_register_exit_event(done);
}

/* Diagnostic for uninterrupted single-thread x86-64 workloads. */
#include "dr_api.h"
#include "drmgr.h"
#include "drreg.h"
#include "drx.h"
#include <limits.h>
#include <stdint.h>
#include <string.h>

enum { INS, DIRECT, INDIRECT, RETURNS, DIVIDE, NCOUNTS };
struct block { int count[NCOUNTS]; };
static uint64_t totals[NCOUNTS];
static volatile int threads;
static file_t output;

static void fail(const char *reason) {
    dr_fprintf(STDERR, "block_events: %s\n", reason);
    dr_abort_with_code(1);
}

static void thread_init(void *context) {
    (void)context;
    if (dr_atomic_add32_return_sum(&threads, 1) != 1)
        fail("multiple application threads");
}

static void fork_init(void *context) {
    (void)context;
    fail("multiple processes");
}

static dr_emit_flags_t analyze(void *ctx, void *tag, instrlist_t *bb, bool trace,
                               bool translating, void **data) {
    (void)tag;
    (void)trace;
    (void)translating;
    struct block *b = dr_thread_alloc(ctx, sizeof(*b));
    memset(b, 0, sizeof(*b));
    bool emulating = false;
    for (instr_t *ins = instrlist_first(bb); ins != NULL; ins = instr_get_next(ins)) {
        instr_t *app = ins;
        if (drmgr_is_emulation_start(ins)) {
            emulated_instr_t em = { .size = sizeof(em) };
            if (emulating || !drmgr_get_emulated_instr_data(ins, &em) || !em.instr)
                fail("invalid emulation start");
            app = em.instr;
            emulating = true;
        } else if (drmgr_is_emulation_end(ins)) {
            if (!emulating)
                fail("invalid emulation end");
            emulating = false;
            continue;
        } else if (emulating || !instr_is_app(ins)) {
            continue;
        }
        if (b->count[INS] == INT_MAX)
            fail("oversized block");
        ++b->count[INS];
        b->count[DIRECT] += instr_is_call_direct(app);
        b->count[INDIRECT] += instr_is_call_indirect(app);
        b->count[RETURNS] += instr_is_return(app);
        int op = instr_get_opcode(app);
        b->count[DIVIDE] += op == OP_div || op == OP_idiv;
    }
    if (emulating)
        fail("unterminated emulation");
    *data = b;
    return DR_EMIT_DEFAULT;
}

static dr_emit_flags_t insert(void *ctx, void *tag, instrlist_t *bb, instr_t *where,
                              bool trace, bool translating, void *data) {
    (void)tag;
    (void)trace;
    (void)translating;
    if (drmgr_is_first_instr(ctx, where)) {
        struct block *b = data;
        for (int i = 0; i < NCOUNTS; ++i) {
            if (b->count[i] && !drx_insert_counter_update(ctx, bb, where, SPILL_SLOT_MAX + 1,
                                                         &totals[i], b->count[i], DRX_COUNTER_64BIT))
                fail("cannot emit inline counter");
        }
        dr_thread_free(ctx, b, sizeof(*b));
    }
    return DR_EMIT_DEFAULT;
}

static void finish(void) {
    if (threads != 1 || totals[INS] == 0)
        fail("incomplete execution");
    dr_fprintf(output, "{\"pid\":%u,\"instructions\":%llu,\"direct\":%llu,\"indirect\":%llu,"
               "\"returns\":%llu,\"divide\":%llu}\n", (unsigned)dr_get_process_id(),
               (unsigned long long)totals[INS], (unsigned long long)totals[DIRECT],
               (unsigned long long)totals[INDIRECT], (unsigned long long)totals[RETURNS],
               (unsigned long long)totals[DIVIDE]);
    dr_close_file(output);
    drmgr_unregister_bb_instrumentation_event(analyze);
    drmgr_unregister_thread_init_event(thread_init);
    drx_exit();
    drreg_exit();
    drmgr_exit();
}

DR_EXPORT void dr_client_main(client_id_t id, int argc, const char *argv[]) {
    (void)id;
    if (argc != 3 || strcmp(argv[1], "-out") != 0)
        fail("expected -out <new-file>");
    output = dr_open_file(argv[2], DR_FILE_WRITE_REQUIRE_NEW | DR_FILE_CLOSE_ON_FORK);
    if (output == INVALID_FILE)
        fail("cannot open output");
    drreg_options_t reg = { .struct_size = sizeof(reg), .num_spill_slots = 3 };
    if (!drmgr_init() || drreg_init(&reg) != DRREG_SUCCESS || !drx_init() ||
        !drmgr_register_thread_init_event(thread_init) ||
        !drmgr_register_bb_instrumentation_event(analyze, insert, NULL))
        fail("cannot register instrumentation");
    dr_register_fork_init_event(fork_init);
    dr_register_exit_event(finish);
}

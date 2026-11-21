/* Trusted, uninterrupted, single-threaded x86-64 measurements. Site state is
 * keyed by the original application PC and survives fragment retranslations. */
#include "dr_api.h"
#include "drmgr.h"
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

enum { PERIOD = 1024, LINE = 64, MAX_SITES = 16384 };
typedef struct {
    app_pc pc;
    uint64_t remaining, previous_line, pairs, near;
    bool pending;
} site_t;
static site_t sites[MAX_SITES];
static site_t *table[MAX_SITES * 2];
static unsigned used;
static app_pc module_start, module_end;
static volatile int threads;
static file_t output;
static process_id_t owner;

static void fail(const char *why) {
    dr_fprintf(STDERR, "access_sample: %s\n", why);
    dr_abort_with_code(1);
}

static site_t *site_for(app_pc pc) {
    size_t slot = ((uintptr_t)pc * UINT64_C(11400714819323198485) >> 49);
    while (table[slot] && table[slot]->pc != pc)
        slot = (slot + 1) % (MAX_SITES * 2);
    if (!table[slot]) {
        if (used == MAX_SITES)
            fail("static load site limit exceeded");
        site_t *s = &sites[used++];
        s->pc = pc;
        s->remaining = 1;
        table[slot] = s;
    }
    return table[slot];
}

/* The arguments contain the original application's base and index values.
 * No target memory is dereferenced and no address is written to the report. */
__attribute__((target("general-regs-only")))
static void observe(site_t *site, uintptr_t base, uintptr_t index, uint64_t spec) {
    uintptr_t address = base + index * (spec >> 32) + (int32_t)spec;
    uint64_t line = address / LINE;
    if (!site->pending) {
        site->previous_line = line;
        site->pending = true;
        site->remaining = 1;
    } else {
        uint64_t prev = site->previous_line;
        if ((line > prev ? line - prev : prev - line) <= 1)
            ++site->near;
        ++site->pairs;
        site->pending = false;
        site->remaining = PERIOD - 1;
    }
}

static bool eligible(instr_t *ins, opnd_t *mem) {
    /* Only these integer opcodes with one explicit flat base/index memory
     * source. Excludes RMW, SIMD, implicit stack/string and RIP-relative loads. */
    switch (instr_get_opcode(ins)) {
    case OP_mov_ld: case OP_movzx: case OP_movsx: case OP_movsxd:
    case OP_cmp: case OP_test: case OP_add: case OP_sub:
    case OP_and: case OP_or: case OP_xor: case OP_imul:
        break;
    default:
        return false;
    }
    if (!instr_reads_memory(ins) || instr_writes_memory(ins))
        return false;
    for (int i = 0; i < instr_num_srcs(ins); ++i) {
        opnd_t o = instr_get_src(ins, i);
        if (!opnd_is_memory_reference(o))
            continue;
        if (!opnd_is_near_base_disp(o) || opnd_is_disp_short_addr(o) ||
            opnd_is_vsib(o) || opnd_get_segment(o) != DR_REG_NULL)
            return false;
        reg_id_t base = opnd_get_base(o), index = opnd_get_index(o);
        if ((base == DR_REG_NULL && index == DR_REG_NULL) ||
            (base != DR_REG_NULL && (!reg_is_gpr(base) || reg_get_size(base) != OPSZ_8)) ||
            (index != DR_REG_NULL && (!reg_is_gpr(index) || reg_get_size(index) != OPSZ_8)))
            return false;
        opnd_size_t size = opnd_get_size(o);
        if (size != OPSZ_1 && size != OPSZ_2 && size != OPSZ_4 && size != OPSZ_8)
            return false;
        *mem = o;
        return true; /* The listed read-only encodings have at most one memory source. */
    }
    return false;
}

/* DEC leaves CF unchanged. Consecutive scalar MOV loads followed by a CMP
 * or loop DEC are common. All other instruction sequences use a gate that
 * preserves every flag without having to save application flags. */
static bool overwrites_dec_flags(instr_t *where) {
    for (instr_t *ins = where; ins; ins = instr_get_next_app(ins)) {
        switch (instr_get_opcode(ins)) {
        case OP_dec: case OP_cmp: case OP_add: case OP_sub:
            return true;
        case OP_mov_ld: case OP_movzx: case OP_movsx: case OP_movsxd:
            break;
        default:
            return false;
        }
    }
    return false;
}

static opnd_t reg_arg(reg_id_t reg) {
    return reg == DR_REG_NULL ? OPND_CREATE_INTPTR(0) : opnd_create_reg(reg);
}

static dr_emit_flags_t insert(void *ctx, void *tag, instrlist_t *bb, instr_t *where,
                              bool trace, bool translating, void *unused) {
    (void)tag; (void)trace; (void)translating; (void)unused;
    if (!instr_is_app(where))
        return DR_EMIT_DEFAULT;
    app_pc pc = instr_get_app_pc(where);
    opnd_t mem;
    if (pc < module_start || pc >= module_end || !eligible(where, &mem))
        return DR_EMIT_DEFAULT;
    site_t *s = site_for(pc);
    opnd_t countdown = opnd_create_rel_addr(&s->remaining, OPSZ_8);
    instr_t *done = INSTR_CREATE_label(ctx);
    bool clobber = overwrites_dec_flags(where);
    if (clobber) {
        instrlist_meta_preinsert(bb, where, INSTR_CREATE_dec(ctx, countdown));
        instrlist_meta_preinsert(bb, where, INSTR_CREATE_jcc(ctx, OP_jnz, opnd_create_instr(done)));
    } else {
        /* JRCXZ and LEA preserve flags. Restore RCX on both paths before any
         * use of the application's memory operand (it may itself use RCX). */
        instr_t *sample = INSTR_CREATE_label(ctx);
        opnd_t rcx = opnd_create_reg(DR_REG_RCX);
        dr_save_reg(ctx, bb, where, DR_REG_RCX, SPILL_SLOT_1);
        instrlist_meta_preinsert(bb, where, INSTR_CREATE_mov_ld(ctx, rcx, countdown));
        instrlist_meta_preinsert(bb, where, INSTR_CREATE_lea(ctx, rcx,
            opnd_create_base_disp(DR_REG_RCX, DR_REG_NULL, 0, -1, OPSZ_lea)));
        instrlist_meta_preinsert(bb, where, INSTR_CREATE_mov_st(ctx, countdown, rcx));
        instrlist_meta_preinsert(bb, where, INSTR_CREATE_jecxz(ctx, opnd_create_instr(sample)));
        dr_restore_reg(ctx, bb, where, DR_REG_RCX, SPILL_SLOT_1);
        instrlist_meta_preinsert(bb, where, INSTR_CREATE_jmp(ctx, opnd_create_instr(done)));
        instrlist_meta_preinsert(bb, where, sample);
        dr_restore_reg(ctx, bb, where, DR_REG_RCX, SPILL_SLOT_1);
    }
    uint64_t spec = (uint64_t)opnd_get_scale(mem) << 32 | (uint32_t)opnd_get_disp(mem);
    dr_insert_clean_call(ctx, bb, where, (void *)observe, false, 4, OPND_CREATE_INTPTR(s),
                         reg_arg(opnd_get_base(mem)), reg_arg(opnd_get_index(mem)),
                         OPND_CREATE_INTPTR(spec));
    instrlist_meta_preinsert(bb, where, done);
    return DR_EMIT_DEFAULT;
}

static void thread_init(void *ctx) {
    (void)ctx;
    if (dr_atomic_add32_return_sum(&threads, 1) != 1)
        fail("multiple application threads");
}

static void fork_init(void *ctx) {
    (void)ctx;
    fail("multiple application processes");
}

static uint64_t visits(site_t *s) {
    if (s->remaining == 0 || s->remaining >= PERIOD || (s->pending && s->remaining != 1) ||
        s->pairs > (UINT64_MAX - PERIOD) / PERIOD || s->near > s->pairs)
        fail("invalid site state");
    if (s->pending)
        return s->pairs * PERIOD + 1;
    return s->pairs ? s->pairs * PERIOD + 1 - s->remaining : 0;
}

static int by_pc(const void *a, const void *b) {
    uintptr_t first = (uintptr_t)((const site_t *)a)->pc, second = (uintptr_t)((const site_t *)b)->pc;
    return (first > second) - (first < second);
}

static void finish(void) {
    if (threads != 1 || owner != dr_get_process_id())
        fail("incomplete single-thread run");
    /* No generated code runs during process_exit; inline site pointers may now move. */
    qsort(sites, used, sizeof(*sites), by_pc);
    uint64_t loads = 0, pairs = 0, near = 0;
    for (unsigned i = 0; i < used; ++i) {
        uint64_t n = visits(&sites[i]);
        if (UINT64_MAX - loads < n)
            fail("load count overflow");
        loads += n;
        near += sites[i].near;
        pairs += sites[i].pairs;
    }
    dr_fprintf(output, "{\"schema\":1,\"pid\":%u,\"threads\":1,\"period\":%d,\"line_bytes\":%d,"
               "\"loads\":%llu,\"pairs\":%llu,\"near_pairs\":%llu,\"far_pairs\":%llu,\"sites\":[",
               (unsigned)owner, PERIOD, LINE, (unsigned long long)loads, (unsigned long long)pairs,
               (unsigned long long)near, (unsigned long long)(pairs - near));
    bool comma = false;
    for (unsigned i = 0; i < used; ++i) {
        site_t *s = &sites[i];
        uint64_t n = visits(s);
        if (!n) continue;
        dr_fprintf(output, "%s{\"offset\":%llu,\"loads\":%llu,\"pairs\":%llu,\"near_pairs\":%llu,"
                   "\"far_pairs\":%llu}", comma ? "," : "", (unsigned long long)(s->pc - module_start),
                   (unsigned long long)n, (unsigned long long)s->pairs, (unsigned long long)s->near,
                   (unsigned long long)(s->pairs - s->near));
        comma = true;
    }
    dr_fprintf(output, "]}\n");
    dr_close_file(output);
    drmgr_unregister_bb_insertion_event(insert);
    drmgr_unregister_thread_init_event(thread_init);
    drmgr_exit();
}

DR_EXPORT void dr_client_main(client_id_t id, int argc, const char *argv[]) {
    (void)id;
    if (argc != 3 || strcmp(argv[1], "-out"))
        fail("expected -out <new output file>");
    output = dr_open_file(argv[2], DR_FILE_WRITE_REQUIRE_NEW | DR_FILE_CLOSE_ON_FORK);
    if (output == INVALID_FILE)
        fail("cannot create output");
    module_data_t *main = dr_get_main_module();
    if (!main)
        fail("missing main executable");
    module_start = main->start;
    module_end = main->end;
    dr_free_module_data(main);
    owner = dr_get_process_id();
    if (!drmgr_init() || !drmgr_register_thread_init_event(thread_init) ||
        !drmgr_register_bb_instrumentation_event(NULL, insert, NULL))
        fail("cannot install sampler");
    dr_register_fork_init_event(fork_init);
    dr_register_exit_event(finish);
}

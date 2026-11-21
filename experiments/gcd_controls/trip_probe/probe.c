/* Diagnostic for trusted single-threaded, immutable x86-64 text. */
#include "dr_api.h"
#include "drmgr.h"
#include <stdint.h>
#include <string.h>

enum { MAX_BODY = 256, MAX_SITES = 1024 };
typedef struct {
    app_pc branch, target;
    uint64_t pending, closed_visits, completed, changes, last;
} site_t;
static site_t sites[MAX_SITES];
static unsigned used;
static app_pc module_start, module_end;
static volatile int threads;
static file_t output;
static process_id_t owner;

static void fail(const char *message) {
    dr_fprintf(STDERR, "trip_probe: %s\n", message);
    dr_abort_with_code(1);
}

static int condition(instr_t *branch) {
    int op = instr_get_opcode(branch);
    if (op >= OP_jo_short && op <= OP_jnle_short)
        return op - OP_jo_short + OP_jo;
    return op >= OP_jo && op <= OP_jnle ? op : OP_INVALID;
}

/* Decode the whole static target-to-branch interval. A first traversal may
 * start in a larger BB, and retranslations may split it at another address. */
static bool eligible_body(void *ctx, app_pc target, app_pc branch) {
    size_t len = branch - target, read = 0;
    byte bytes[MAX_BODY + MAX_INSTR_LENGTH] = {0}, *cursor = bytes;
    if (!dr_safe_read(target, len, bytes, &read) || read != len)
        fail("unreadable main-ELF backward branch body");
    bool eligible = false;
    instr_t ins;
    instr_init(ctx, &ins);
    while (cursor < bytes + len) {
        byte *next = decode_from_copy(ctx, cursor, target + (cursor - bytes), &ins);
        if (!next || next > bytes + len || instr_is_cti(&ins) || instr_is_syscall(&ins)) {
            eligible = false;
            break;
        }
        if (instr_get_opcode(&ins) == OP_div && opnd_get_size(instr_get_src(&ins, 0)) == OPSZ_4)
            eligible = true;
        cursor = next;
        instr_reset(ctx, &ins);
    }
    instr_free(ctx, &ins);
    return eligible;
}

static dr_emit_flags_t analyze(void *ctx, void *tag, instrlist_t *bb, bool trace,
                               bool translating, void **data) {
    (void)tag; (void)trace; (void)translating;
    *data = NULL;
    instr_t *branch = instrlist_last_app(bb);
    if (!branch || !instr_is_cbr(branch) || condition(branch) == OP_INVALID)
        return DR_EMIT_DEFAULT;
    app_pc pc = instr_get_app_pc(branch);
    opnd_t dest = instr_get_target(branch);
    if (pc < module_start || pc >= module_end || !opnd_is_pc(dest))
        return DR_EMIT_DEFAULT;
    app_pc target = opnd_get_pc(dest);
    if (target < module_start || target >= pc || (size_t)(pc - target) > MAX_BODY)
        return DR_EMIT_DEFAULT;
    for (unsigned i = 0; i < used; ++i) {
        if (sites[i].branch == pc) {
            if (sites[i].target != target)
                fail("changed branch target");
            *data = &sites[i];
            return DR_EMIT_DEFAULT;
        }
    }
    if (!eligible_body(ctx, target, pc))
        return DR_EMIT_DEFAULT;
    if (used == MAX_SITES)
        fail("site capacity exceeded");
    site_t *site = &sites[used++];
    site->branch = pc;
    site->target = target;
    *data = site;
    return DR_EMIT_DEFAULT;
}

/* No SIMD state is touched by the exit clean call. */
__attribute__((target("general-regs-only")))
static void complete(site_t *s) {
    uint64_t length = s->pending;
    if (!length || length > UINT64_MAX - s->closed_visits)
        fail("visit counter overflow");
    s->changes += s->completed && s->last != length;
    s->closed_visits += length;
    ++s->completed;
    s->last = length;
    s->pending = 0;
}

static bool can_insert_inc_before(instr_t *ins) {
    if (!ins)
        return false;
    /* INC preserves CF; every flag it changes is overwritten before use. */
    switch (instr_get_opcode(ins)) {
    case OP_cmp: case OP_test: case OP_sub: case OP_add: case OP_dec: case OP_inc:
        return true;
    default:
        return false;
    }
}

static dr_emit_flags_t insert(void *ctx, void *tag, instrlist_t *bb, instr_t *where,
                              bool trace, bool translating, void *data) {
    (void)tag; (void)trace; (void)translating;
    site_t *s = data;
    if (!s || !instr_is_app(where))
        return DR_EMIT_DEFAULT;
    instr_t *branch = instrlist_last_app(bb), *before = instr_get_prev_app(branch);
    opnd_t pending = opnd_create_rel_addr(&s->pending, OPSZ_8);
    if (where == before && can_insert_inc_before(before))
        instrlist_meta_preinsert(bb, where, INSTR_CREATE_inc(ctx, pending));
    if (where != branch)
        return DR_EMIT_DEFAULT;
    if (!can_insert_inc_before(before)) {
        /* LEA between two MOVs preserves all live application flags. */
        opnd_t rcx = opnd_create_reg(DR_REG_RCX);
        dr_save_reg(ctx, bb, where, DR_REG_RCX, SPILL_SLOT_1);
        instrlist_meta_preinsert(bb, where, INSTR_CREATE_mov_ld(ctx, rcx, pending));
        instrlist_meta_preinsert(bb, where, INSTR_CREATE_lea(ctx, rcx,
            opnd_create_base_disp(DR_REG_RCX, DR_REG_NULL, 0, 1, OPSZ_lea)));
        instrlist_meta_preinsert(bb, where, INSTR_CREATE_mov_st(ctx, pending, rcx));
        dr_restore_reg(ctx, bb, where, DR_REG_RCX, SPILL_SLOT_1);
    }
    instr_t *taken = INSTR_CREATE_label(ctx);
    instrlist_meta_preinsert(bb, where, INSTR_CREATE_jcc(ctx, condition(branch), opnd_create_instr(taken)));
    dr_insert_clean_call(ctx, bb, where, (void *)complete, false, 1, OPND_CREATE_INTPTR(s));
    instrlist_meta_preinsert(bb, where, taken);
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

static void finish(void) {
    if (threads != 1 || owner != dr_get_process_id())
        fail("incomplete single-thread run");
    dr_fprintf(output, "{\"schema\":1,\"pid\":%u,\"threads\":1,\"sites\":[", (unsigned)owner);
    bool comma = false;
    for (unsigned i = 0; i < used; ++i) {
        site_t *s = &sites[i];
        if (!s->closed_visits && !s->pending)
            continue;
        if (s->pending > UINT64_MAX - s->closed_visits)
            fail("total visit overflow");
        dr_fprintf(output, "%s{\"branch_offset\":%llu,\"target_offset\":%llu,\"visits\":%llu,"
                   "\"completed\":%llu,\"changes\":%llu,\"last_length\":%llu,\"pending_length\":%llu}",
                   comma ? "," : "", (unsigned long long)(s->branch - module_start),
                   (unsigned long long)(s->target - module_start),
                   (unsigned long long)(s->closed_visits + s->pending),
                   (unsigned long long)s->completed, (unsigned long long)s->changes,
                   (unsigned long long)s->last, (unsigned long long)s->pending);
        comma = true;
    }
    dr_fprintf(output, "]}\n");
    dr_close_file(output);
    drmgr_unregister_bb_instrumentation_event(analyze);
    drmgr_unregister_thread_init_event(thread_init);
    drmgr_exit();
}

DR_EXPORT void dr_client_main(client_id_t id, int argc, const char *argv[]) {
    (void)id;
    if (argc != 3 || strcmp(argv[1], "-out"))
        fail("expected -out <new file>");
    output = dr_open_file(argv[2], DR_FILE_WRITE_REQUIRE_NEW | DR_FILE_CLOSE_ON_FORK);
    if (output == INVALID_FILE)
        fail("cannot create output");
    module_data_t *main = dr_get_main_module();
    if (!main)
        fail("main executable unavailable");
    module_start = main->start;
    module_end = main->end;
    dr_free_module_data(main);
    owner = dr_get_process_id();
    if (!drmgr_init() || !drmgr_register_thread_init_event(thread_init) ||
        !drmgr_register_bb_instrumentation_event(analyze, insert, NULL))
        fail("cannot install trip probe");
    dr_register_fork_init_event(fork_init);
    dr_register_exit_event(finish);
}

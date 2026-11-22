/* One bin counts executions of a straight-line group containing N omitted
 * instructions. This makes the hot increment always one, so a flag-neutral
 * JRCXZ can detect its wrap even when arithmetic flags are live. */
enum { MAX_GROUP = 64 };
static uint64_t executions[MAX_GROUP + 1];
static uint32_t wrapped;
static volatile int saw_signal;
static ptr_uint_t group_note;
static file_t guard_file;

static dr_signal_action_t mark_signal(void *ctx, dr_siginfo_t *info) {
    (void)ctx; (void)info;
    dr_atomic_store32(&saw_signal, 1);
    return DR_SIGNAL_DELIVER;
}

static void mark_group(void *ctx, instrlist_t *bb, instr_t *last, unsigned count) {
    if (!count)
        return;
    instr_t *label = INSTR_CREATE_label(ctx);
    instr_set_note(label, (void *)group_note);
    dr_instr_label_data_t *data = instr_get_label_data_area(label);
    data->data[0] = count;
    data->data[1] = overwrites_dec_flags(last);
    instrlist_meta_preinsert(bb, last, label);
}

static dr_emit_flags_t analyze_omitted(void *ctx, void *tag, instrlist_t *bb, bool trace,
                                      bool translating, void **unused) {
    (void)tag; (void)trace; (void)translating; (void)unused;
    unsigned count = 0;
    instr_t *last = NULL;
    for (instr_t *ins = instrlist_first_app(bb); ins; ins = instr_get_next_app(ins)) {
        app_pc pc = instr_get_app_pc(ins);
        if (instr_is_cti(ins) || instr_is_syscall(ins) || instr_is_interrupt(ins) ||
            pc < module_start || pc >= module_end) {
            mark_group(ctx, bb, last, count);
            count = 0;
            continue;
        }
        opnd_t mem;
        if (!selected((uintptr_t)pc - (uintptr_t)module_start) && eligible(ins, &mem)) {
            last = ins;
            if (++count == MAX_GROUP) {
                mark_group(ctx, bb, last, count);
                count = 0;
            }
        }
    }
    mark_group(ctx, bb, last, count);
    return DR_EMIT_DEFAULT;
}

static void insert_omitted(void *ctx, instrlist_t *bb, instr_t *label) {
    dr_instr_label_data_t *data = instr_get_label_data_area(label);
    opnd_t bin = opnd_create_rel_addr(&executions[data->data[0]], OPSZ_8);
    instr_t *done = INSTR_CREATE_label(ctx);
    opnd_t rcx = opnd_create_reg(DR_REG_RCX);
    if (data->data[1]) {
        /* INC and the inherited DEC gate clobber the same flags (never CF). */
        instrlist_meta_preinsert(bb, label, INSTR_CREATE_inc(ctx, bin));
        instrlist_meta_preinsert(bb, label, INSTR_CREATE_jcc(ctx, OP_jnz, opnd_create_instr(done)));
    } else {
        dr_save_reg(ctx, bb, label, DR_REG_RCX, SPILL_SLOT_2);
        instrlist_meta_preinsert(bb, label, INSTR_CREATE_mov_ld(ctx, rcx, bin));
        instrlist_meta_preinsert(bb, label, INSTR_CREATE_lea(ctx, rcx,
            opnd_create_base_disp(DR_REG_RCX, DR_REG_NULL, 0, 1, OPSZ_lea)));
        instrlist_meta_preinsert(bb, label, INSTR_CREATE_mov_st(ctx, bin, rcx));
        instr_t *overflow = INSTR_CREATE_label(ctx);
        instrlist_meta_preinsert(bb, label, INSTR_CREATE_jecxz(ctx, opnd_create_instr(overflow)));
        instrlist_meta_preinsert(bb, label, INSTR_CREATE_jmp(ctx, opnd_create_instr(done)));
        instrlist_meta_preinsert(bb, label, overflow);
    }
    /* Sticky even if a bin wraps more than once. Checked before publishing. */
    instrlist_meta_preinsert(bb, label, INSTR_CREATE_mov_st(ctx,
        opnd_create_rel_addr(&wrapped, OPSZ_4), OPND_CREATE_INT32(1)));
    instrlist_meta_preinsert(bb, label, done);
    if (!data->data[1])
        dr_restore_reg(ctx, bb, label, DR_REG_RCX, SPILL_SLOT_2);
}

#include "guard_math.h"

static void write_guard(uint64_t selected_loads) {
    if (wrapped || dr_atomic_load32(&saw_signal))
        fail("guard count overflow or interrupted execution");
    uint64_t omitted = 0;
    for (unsigned weight = 1; weight <= MAX_GROUP; ++weight) {
        if (!add_weighted(&omitted, executions[weight], weight))
            fail("omitted load count overflow");
    }
    if (selected_loads > UINT64_MAX - omitted)
        fail("combined load count overflow");
    dr_fprintf(guard_file, "{\"schema\":1,\"arm\":\"guarded\","
               "\"scope\":\"main_module_eligible_scalar_reads\",\"pid\":%u,"
               "\"threads\":1,\"omitted_loads\":%llu}\n", (unsigned)owner,
               (unsigned long long)omitted);
    dr_close_file(guard_file);
}

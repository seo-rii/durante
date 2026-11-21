/* Research client for trusted, uninterrupted, single-threaded native programs.
 * The hot path counts one entry per main-ELF divide block. The dependency
 * description is captured once at translation so candidate clocks can be
 * replayed without instrumenting the individual divides. */
#include "dr_api.h"
#include "drmgr.h"
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "latency_bound.h"

enum { MAX_BLOCKS = 4096, MAX_OPS = 1024, MAX_DEPS = 32 };
typedef struct {
    unsigned n;
    uint16_t slot[MAX_DEPS];
} deps_t;
typedef struct {
    uintptr_t offset;
    int opcode;
    unsigned latency, div_bits, load_delay;
    bool zero;
    deps_t reads, load_reads, writes;
} op_t;
typedef struct {
    uintptr_t offset;
    uint64_t entries;
    unsigned count, v1_x16;
    bool self_loop;
    op_t ops[];
} block_t;

static block_t *blocks[MAX_BLOCKS];
static unsigned used;
static app_pc first, end;
static file_t output;
static process_id_t owner;
static volatile int threads;

static void fail(const char *why) {
    dr_fprintf(STDERR, "divide_clock: %s\n", why);
    dr_abort_with_code(1);
}

static void add_dep(deps_t *deps, int reg) {
    if (reg < 0)
        return;
    unsigned at = 0;
    while (at < deps->n && deps->slot[at] < reg)
        ++at;
    if (at < deps->n && deps->slot[at] == reg)
        return;
    if (deps->n == MAX_DEPS)
        fail("too many dependencies in one instruction");
    memmove(&deps->slot[at + 1], &deps->slot[at], (deps->n - at) * sizeof(deps->slot[0]));
    deps->slot[at] = (uint16_t)reg;
    ++deps->n;
}

static void registers(deps_t *deps, opnd_t operand) {
    for (int i = 0; i < opnd_num_regs_used(operand); ++i)
        add_dep(deps, durante_latency_slot(opnd_get_reg_used(operand, i)));
}

static void describe(op_t *op, instr_t *ins) {
    app_pc pc = instr_get_app_pc(ins);
    if (pc < first || pc >= end)
        fail("divide block crosses executable boundary");
    op->offset = (uintptr_t)(pc - first);
    op->opcode = instr_get_opcode(ins);
    op->latency = (unsigned)durante_latency_cycles(ins);
    if (op->opcode == OP_div || op->opcode == OP_idiv) {
        op->div_bits = 8 * opnd_size_in_bytes(opnd_get_size(instr_get_src(ins, 0)));
        if (op->div_bits != 8 && op->div_bits != 16 && op->div_bits != 32 && op->div_bits != 64)
            fail("unknown divide width");
    }
    if (op->opcode == OP_xor && instr_num_srcs(ins) == 2 && instr_num_dsts(ins) == 1) {
        opnd_t a = instr_get_src(ins, 0);
        opnd_size_t size = opnd_get_size(a);
        op->zero = opnd_is_reg(a) && reg_is_gpr(opnd_get_reg(a)) &&
                   (size == OPSZ_4 || size == OPSZ_8) &&
                   opnd_same(a, instr_get_src(ins, 1)) && opnd_same(a, instr_get_dst(ins, 0));
    }
    for (int i = 0; i < instr_num_srcs(ins); ++i) {
        opnd_t o = instr_get_src(ins, i);
        if (!opnd_is_memory_reference(o) || op->opcode == OP_lea)
            registers(&op->reads, o);
        else if (op->opcode != OP_nop_modrm) {
            op->load_delay = 4;
            registers(&op->load_reads, o);
        }
    }
    for (int i = 0; i < instr_num_dsts(ins); ++i) {
        opnd_t o = instr_get_dst(ins, i);
        if (opnd_is_memory_reference(o))
            registers(&op->reads, o);
        else if (opnd_is_reg(o))
            add_dep(&op->writes, durante_latency_slot(opnd_get_reg(o)));
    }
    uint flags = instr_get_eflags(ins, DR_QUERY_DEFAULT);
    if (flags & EFLAGS_READ_ALL)
        add_dep(&op->reads, DURANTE_LATENCY_FLAGS_SLOT);
    if (flags & EFLAGS_WRITE_ALL)
        add_dep(&op->writes, DURANTE_LATENCY_FLAGS_SLOT);
}

static dr_emit_flags_t analyze(void *ctx, void *tag, instrlist_t *bb, bool trace,
                               bool translating, void **data) {
    (void)trace;
    (void)translating;
    *data = NULL;
    app_pc pc = dr_fragment_app_pc(tag);
    if (pc < first || pc >= end)
        return DR_EMIT_DEFAULT;
    bool has_div = false;
    unsigned count = 0;
    for (instr_t *ins = instrlist_first_app(bb); ins; ins = instr_get_next_app(ins)) {
        int opcode = instr_get_opcode(ins);
        has_div |= opcode == OP_div || opcode == OP_idiv;
        ++count;
    }
    if (!has_div)
        return DR_EMIT_DEFAULT;
    if (count > MAX_OPS)
        fail("divide block instruction limit exceeded");
    /* Production counts an emulation region once. The native workload uses
     * none inside the selected blocks. Refuse mixed blocks instead of
     * guessing a representation with a different instruction count. */
    for (instr_t *ins = instrlist_first(bb); ins; ins = instr_get_next(ins))
        if (drmgr_is_emulation_start(ins) || drmgr_is_emulation_end(ins))
            fail("emulated instruction in divide block");
    size_t size = sizeof(block_t) + count * sizeof(op_t);
    block_t *record = dr_global_alloc(size);
    if (!record)
        fail("cannot allocate block descriptor");
    memset(record, 0, size);
    record->offset = (uintptr_t)(pc - first);
    record->count = count;
    instr_t *last = instrlist_last_app(bb);
    record->self_loop = instr_is_cbr(last) && opnd_is_pc(instr_get_target(last)) &&
                        opnd_get_pc(instr_get_target(last)) == pc;
    record->v1_x16 = durante_latency_bound_x16(ctx, tag, bb, count);
    if (record->v1_x16 == UINT32_MAX)
        fail("production latency bound unavailable");
    unsigned i = 0;
    for (instr_t *ins = instrlist_first_app(bb); ins; ins = instr_get_next_app(ins))
        describe(&record->ops[i++], ins);
    for (i = 0; i < used; ++i) {
        block_t *other = blocks[i];
        if (record->offset == other->offset && record->count == other->count &&
            record->self_loop == other->self_loop && record->v1_x16 == other->v1_x16 &&
            !memcmp(record->ops, other->ops, count * sizeof(op_t))) {
            *data = other;
            dr_global_free(record, size);
            return DR_EMIT_DEFAULT;
        }
    }
    if (used == MAX_BLOCKS)
        fail("divide block descriptor limit exceeded");
    blocks[used++] = record;
    *data = record;
    return DR_EMIT_DEFAULT;
}

__attribute__((target("general-regs-only")))
static void count_entry(block_t *record) {
    if (record->entries == UINT64_MAX)
        fail("block entry overflow");
    ++record->entries;
}

static dr_emit_flags_t insert(void *ctx, void *tag, instrlist_t *bb, instr_t *where,
                              bool trace, bool translating, void *data) {
    (void)tag;
    (void)trace;
    (void)translating;
    if (data && where == instrlist_first_app(bb))
        dr_insert_clean_call(ctx, bb, where, (void *)count_entry, false, 1, OPND_CREATE_INTPTR(data));
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

static void write_deps(const deps_t *deps) {
    dr_fprintf(output, "[");
    for (unsigned i = 0; i < deps->n; ++i)
        dr_fprintf(output, "%s%u", i ? "," : "", (unsigned)deps->slot[i]);
    dr_fprintf(output, "]");
}

static void write_op(const op_t *op) {
    dr_fprintf(output, "{\"offset\":%llu,\"opcode\":\"%s\",\"latency\":%u,\"div_bits\":%u,"
               "\"zero\":%s,\"load_delay\":%u,\"reads\":", (unsigned long long)op->offset,
               decode_opcode_name(op->opcode), op->latency, op->div_bits, op->zero ? "true" : "false",
               op->load_delay);
    write_deps(&op->reads);
    dr_fprintf(output, ",\"load_reads\":");
    write_deps(&op->load_reads);
    dr_fprintf(output, ",\"writes\":");
    write_deps(&op->writes);
    dr_fprintf(output, "}");
}

static void finish(void) {
    if (owner != dr_get_process_id() || threads != 1)
        fail("incomplete single-thread run");
    dr_fprintf(output, "{\"schema\":1,\"pid\":%u,\"threads\":1,\"blocks\":[", (unsigned)owner);
    bool comma = false;
    for (unsigned i = 0; i < used; ++i) {
        block_t *b = blocks[i];
        if (b->entries) {
            dr_fprintf(output, "%s{\"offset\":%llu,\"entries\":%llu,\"self_loop\":%s,"
                       "\"instructions\":%u,\"v1_x16\":%u,\"ops\":[", comma ? "," : "",
                       (unsigned long long)b->offset, (unsigned long long)b->entries,
                       b->self_loop ? "true" : "false", b->count, b->v1_x16);
            for (unsigned j = 0; j < b->count; ++j) {
                if (j) dr_fprintf(output, ",");
                write_op(&b->ops[j]);
            }
            dr_fprintf(output, "]}");
            comma = true;
        }
        dr_global_free(b, sizeof(block_t) + b->count * sizeof(op_t));
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
        fail("missing executable");
    first = main->start;
    end = main->end;
    dr_free_module_data(main);
    owner = dr_get_process_id();
    if (!drmgr_init() || !drmgr_register_thread_init_event(thread_init) ||
        !drmgr_register_bb_instrumentation_event(analyze, insert, NULL))
        fail("cannot register instrumentation");
    dr_register_fork_init_event(fork_init);
    dr_register_exit_event(finish);
}

/* Trusted, uninterrupted, single-threaded x86-64 main executable. */
#include "dr_api.h"
#include "drmgr.h"
#include <stdint.h>
#include <string.h>

enum { PERIOD = 1021 };
static app_pc terminal;
static uintptr_t offset;
static file_t output;
static process_id_t owner;
static volatile int threads;
static uint64_t remaining = 1, previous_line, pairs, suffix_sum;
static bool pending;
static uint64_t histogram[61];

static void fail(const char *message) {
    dr_fprintf(STDERR, "search_probe: %s\n", message);
    dr_abort_with_code(1);
}

/* A prefix of a terminal element index is independent of the runtime address
 * of the slice. Compare 16-element regions relative to its base. */
__attribute__((target("general-regs-only")))
static void sample(uint64_t index) {
    uint64_t line = index >> 4;
    if (!pending) {
        previous_line = line;
        remaining = 1;
    } else {
        uint64_t changed = previous_line ^ line;
        unsigned width = changed ? 64u - (unsigned)__builtin_clzll(changed) : 0u;
        if (suffix_sum > UINT64_MAX - width || pairs >= UINT64_MAX / PERIOD)
            fail("sample count exceeds report range");
        suffix_sum += width;
        ++histogram[width];
        ++pairs;
        remaining = PERIOD - 1;
    }
    pending = !pending;
}

/* Accept only the registered 32-bit comparison of a table entry with a
 * register, with a flat 64-bit base and 4-byte scaled index. */
static reg_id_t index_register(instr_t *ins) {
    if (instr_get_opcode(ins) != OP_cmp || instr_num_srcs(ins) != 2 ||
        instr_writes_memory(ins))
        return DR_REG_NULL;
    opnd_t a = instr_get_src(ins, 0), b = instr_get_src(ins, 1);
    opnd_t mem = opnd_is_memory_reference(a) ? a : b;
    opnd_t val = opnd_is_memory_reference(a) ? b : a;
    if (!opnd_is_near_base_disp(mem) || opnd_get_size(mem) != OPSZ_4 ||
        opnd_get_scale(mem) != 4 || opnd_get_disp(mem) != 0 ||
        opnd_get_segment(mem) != DR_REG_NULL || opnd_is_disp_short_addr(mem) ||
        opnd_is_vsib(mem) || !opnd_is_reg(val) || opnd_get_size(val) != OPSZ_4)
        return DR_REG_NULL;
    reg_id_t base = opnd_get_base(mem), index = opnd_get_index(mem);
    if (!reg_is_gpr(base) || !reg_is_gpr(index) ||
        reg_get_size(base) != OPSZ_8 || reg_get_size(index) != OPSZ_8)
        return DR_REG_NULL;
    return index;
}

static dr_emit_flags_t insert(void *ctx, void *tag, instrlist_t *bb, instr_t *where,
                              bool trace, bool translating, void *unused) {
    (void)tag; (void)trace; (void)translating; (void)unused;
    if (!instr_is_app(where) || instr_get_app_pc(where) != terminal)
        return DR_EMIT_DEFAULT;
    reg_id_t index = index_register(where);
    if (index == DR_REG_NULL)
        fail("registered instruction changed");
    /* The following application CMP overwrites all arithmetic flags; DEC
     * neither needs a scratch register nor changes any other live state. */
    instr_t *done = INSTR_CREATE_label(ctx);
    instrlist_meta_preinsert(bb, where, INSTR_CREATE_dec(ctx, opnd_create_rel_addr(&remaining, OPSZ_8)));
    instrlist_meta_preinsert(bb, where, INSTR_CREATE_jcc(ctx, OP_jnz, opnd_create_instr(done)));
    dr_insert_clean_call(ctx, bb, where, (void *)sample, false, 1, opnd_create_reg(index));
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

static void finish(void) {
    if (threads != 1 || owner != dr_get_process_id())
        fail("incomplete single-process run");
    if (!remaining || remaining >= PERIOD || (pending && remaining != 1))
        fail("invalid sampling countdown");
    uint64_t visits = pairs * PERIOD;
    if (pending)
        ++visits;
    else if (pairs)
        visits -= remaining - 1;
    dr_fprintf(output, "{\"schema\":1,\"pid\":%u,\"threads\":1,\"pc\":%llu,"
               "\"visits\":%llu,\"pairs\":%llu,\"suffix_sum\":%llu,\"suffix_histogram\":[",
               (unsigned)owner, (unsigned long long)offset, (unsigned long long)visits,
               (unsigned long long)pairs, (unsigned long long)suffix_sum);
    for (unsigned width = 0; width < sizeof(histogram) / sizeof(histogram[0]); ++width)
        dr_fprintf(output, "%s%llu", width ? "," : "", (unsigned long long)histogram[width]);
    dr_fprintf(output, "]}\n");
    dr_close_file(output);
    drmgr_unregister_bb_insertion_event(insert);
    drmgr_unregister_thread_init_event(thread_init);
    drmgr_exit();
}

static uintptr_t parse_hex(const char *arg) {
    if (arg[0] == '0' && (arg[1] == 'x' || arg[1] == 'X'))
        arg += 2;
    if (!*arg)
        fail("empty hexadecimal offset");
    uintptr_t result = 0;
    for (; *arg; ++arg) {
        unsigned digit;
        if (*arg >= '0' && *arg <= '9') digit = *arg - '0';
        else if (*arg >= 'a' && *arg <= 'f') digit = *arg - 'a' + 10;
        else if (*arg >= 'A' && *arg <= 'F') digit = *arg - 'A' + 10;
        else { fail("invalid hexadecimal offset"); return 0; }
        if (result > (UINTPTR_MAX - digit) / 16)
            fail("offset overflow");
        result = 16 * result + digit;
    }
    return result;
}

static void check_site(module_data_t *main) {
    if (offset >= (uintptr_t)(main->end - main->start))
        fail("offset outside main executable");
    terminal = main->start + offset;
    size_t available = 0;
    for (uint i = 0; i < main->num_segments; ++i) {
        const module_segment_data_t *s = &main->segments[i];
        if ((s->prot & DR_MEMPROT_EXEC) && s->start <= terminal && terminal < s->end) {
            available = s->end - terminal;
            break;
        }
    }
    byte bytes[2 * MAX_INSTR_LENGTH] = {0};
    size_t read = 0;
    if (available > MAX_INSTR_LENGTH)
        available = MAX_INSTR_LENGTH;
    if (!available || !dr_safe_read(terminal, available, bytes, &read) || read != available)
        fail("unreadable terminal instruction");
    void *ctx = dr_get_current_drcontext();
    instr_t ins;
    instr_init(ctx, &ins);
    byte *end = decode_from_copy(ctx, bytes, terminal, &ins);
    if (!end || end > bytes + read || index_register(&ins) == DR_REG_NULL)
        fail("offset does not contain CMP of dword [base + index*4] and register");
    instr_free(ctx, &ins);
}

DR_EXPORT void dr_client_main(client_id_t id, int argc, const char *argv[]) {
    (void)id;
    if (argc != 5 || strcmp(argv[1], "-pc") || strcmp(argv[3], "-out"))
        fail("expected -pc HEX -out FILE");
    offset = parse_hex(argv[2]);
    module_data_t *main = dr_get_main_module();
    if (!main)
        fail("missing main executable");
    check_site(main);
    dr_free_module_data(main);
    output = dr_open_file(argv[4], DR_FILE_WRITE_REQUIRE_NEW | DR_FILE_CLOSE_ON_FORK);
    if (output == INVALID_FILE)
        fail("cannot create output");
    owner = dr_get_process_id();
    if (!drmgr_init() || !drmgr_register_thread_init_event(thread_init) ||
        !drmgr_register_bb_instrumentation_event(NULL, insert, NULL))
        fail("cannot install sampler");
    dr_register_fork_init_event(fork_init);
    dr_register_exit_event(finish);
}

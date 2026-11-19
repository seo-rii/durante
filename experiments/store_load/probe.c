/* Trusted uninterrupted, single-thread, single-image Linux x86-64 fixtures.
 * With traces disabled, count only adjacent original store/load MOVs within
 * one basic block. A store from a GPR changes no address registers, so both
 * effective addresses can be evaluated immediately before the attempted load. */
#include "dr_api.h"
#include "drmgr.h"
#include "../../client/durante_runtime.h"
/* Let an unpatched runtime report why the experiment cannot start. */
#pragma weak dr_durante_runtime_abi_version
#include "range.h"

static uint64_t counts[3];
static volatile int threads;

static void fail(const char *message) {
  dr_fprintf(STDERR, "store_load_probe: %s\n", message);
  dr_abort_with_code(1);
}

static void thread_init(void *ctx) {
  (void)ctx;
  if (dr_atomic_add32_return_sum(&threads, 1) != 1)
    fail("requires a single application thread");
}

static void fork_init(void *ctx) {
  (void)ctx;
  fail("requires a single process");
}

static void record(uintptr_t sb, uintptr_t si, unsigned ss, intptr_t sd, unsigned sw,
                   uintptr_t lb, uintptr_t li, unsigned ls, intptr_t ld, unsigned lw) {
  range_relation_t relation = range_relation(sb + si * ss + sd, sw, lb + li * ls + ld, lw);
  if (relation == INVALID_RANGE || counts[relation] == UINT64_MAX)
    fail("invalid range or counter overflow");
  ++counts[relation];
}

static bool address_reg(reg_id_t reg) {
  return reg == DR_REG_NULL || (reg_is_gpr(reg) && reg_get_size(reg) == OPSZ_8);
}

static bool supported(opnd_t mem) {
  if (!opnd_is_near_base_disp(mem) || opnd_is_disp_short_addr(mem) || opnd_is_vsib(mem))
    return false;
  unsigned size = opnd_size_in_bytes(opnd_get_size(mem));
  return (size == 1 || size == 2 || size == 4 || size == 8) &&
         address_reg(opnd_get_base(mem)) && address_reg(opnd_get_index(mem));
}

static opnd_t value(reg_id_t reg) {
  return reg == DR_REG_NULL ? OPND_CREATE_INTPTR(0) : opnd_create_reg(reg);
}

static dr_emit_flags_t insert(void *ctx, void *tag, instrlist_t *bb, instr_t *load,
                              bool for_trace, bool translating, void *user_data) {
  (void)tag;
  (void)translating;
  (void)user_data;
  if (for_trace)
    fail("run with -disable_traces");
  if (!instr_is_app(load) || instr_get_opcode(load) != OP_mov_ld ||
      instr_num_srcs(load) != 1 || instr_num_dsts(load) != 1)
    return DR_EMIT_DEFAULT;
  instr_t *store = instr_get_prev_app(load);
  if (!store || instr_get_opcode(store) != OP_mov_st ||
      instr_num_srcs(store) != 1 || instr_num_dsts(store) != 1)
    return DR_EMIT_DEFAULT;
  opnd_t from = instr_get_src(store, 0), to = instr_get_dst(load, 0);
  opnd_t s = instr_get_dst(store, 0), l = instr_get_src(load, 0);
  if (!opnd_is_reg(from) || !reg_is_gpr(opnd_get_reg(from)) ||
      !opnd_is_reg(to) || !reg_is_gpr(opnd_get_reg(to)) || !supported(s) || !supported(l))
    return DR_EMIT_DEFAULT;
  app_pc pc = instr_get_app_pc(store);
  if (!pc || pc + instr_length(ctx, store) != instr_get_app_pc(load))
    return DR_EMIT_DEFAULT;
  dr_insert_clean_call(ctx, bb, load, (void *)record, false, 10,
      value(opnd_get_base(s)), value(opnd_get_index(s)), OPND_CREATE_INT32(opnd_get_scale(s)),
      OPND_CREATE_INTPTR((intptr_t)opnd_get_disp(s)), OPND_CREATE_INT32(opnd_size_in_bytes(opnd_get_size(s))),
      value(opnd_get_base(l)), value(opnd_get_index(l)), OPND_CREATE_INT32(opnd_get_scale(l)),
      OPND_CREATE_INTPTR((intptr_t)opnd_get_disp(l)), OPND_CREATE_INT32(opnd_size_in_bytes(opnd_get_size(l))));
  return DR_EMIT_DEFAULT;
}

static void finish(void) {
  dr_fprintf(STDERR, "STORE_LOAD_V1 covered=%llu partial=%llu disjoint=%llu\n",
             (unsigned long long)counts[COVERED], (unsigned long long)counts[PARTIAL],
             (unsigned long long)counts[DISJOINT]);
  drmgr_unregister_bb_insertion_event(insert);
  drmgr_unregister_thread_init_event(thread_init);
  drmgr_exit();
}

DR_EXPORT void dr_client_main(client_id_t id, int argc, const char **argv) {
  (void)id;
  (void)argv;
  uint64 ignore_eflags;
  if (dr_durante_runtime_abi_version == NULL ||
      dr_durante_runtime_abi_version() != DR_DURANTE_RUNTIME_ABI_VERSION)
    fail("requires the prepared Durante runtime");
  if (!dr_get_integer_option("cleancall_ignore_eflags", &ignore_eflags) || ignore_eflags)
    fail("requires full clean-call flag preservation");
  if (argc != 1 || !drmgr_init() || !drmgr_register_thread_init_event(thread_init) ||
      !drmgr_register_bb_instrumentation_event(NULL, insert, NULL))
    fail("unable to initialize client or invalid arguments");
  dr_register_fork_init_event(fork_init);
  dr_register_exit_event(finish);
}

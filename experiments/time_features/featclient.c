/* Exploratory feature client: per-BB static instruction classes counted at BB
 * entry, and optionally a gshare branch-predictor simulation. Single-threaded
 * workloads only; not a production meter. */
#include "dr_api.h"
#include "drmgr.h"
#include "../../client/durante_runtime.h"
/* Full feature mode can still run on the unpatched research SDK. */
#pragma weak dr_durante_runtime_abi_version
#include "hashtable.h"
#include <stdint.h>
#include <string.h>

enum { F_INSTR, F_LOAD, F_STORE, F_CBR, F_UBR, F_MBR, F_MUL, F_DIV, F_FP,
       F_CRITICAL_X16, F_RECURRENCE_X16, F_BOUND_X16, F_COUNT };
static const char *names[F_COUNT] = {"instructions", "loads", "stores", "cond_branches", "direct_jumps_calls", "indirect_branches", "muls", "divs", "fp_ops",
                                     "critical_path_cycles_x16", "loop_recurrence_cycles_x16", "bound_cycles_x16"};
typedef struct block { struct block *next; uint64_t executions; uint32_t f[F_COUNT]; } block_t;
static block_t *blocks;
static void *lock;
static char output[1024];
static bool bpred, recurrence_only;
static uint64_t recurrence_total;
#define BP_BITS 14
static uint8_t table[1 << BP_BITS];
static uint32_t history;
static uint64_t cbr_executed, mispredicts;

static void on_cbr(app_pc inst, app_pc target, int taken) {
  (void)target;
  uint32_t idx = (uint32_t)(((uintptr_t)inst >> 1) ^ history) & ((1u << BP_BITS) - 1);
  uint8_t c = table[idx];
  int predict = c >= 2;
  if (predict != taken) mispredicts++;
  table[idx] = taken ? (c < 3 ? c + 1 : 3) : (c > 0 ? c - 1 : 0);
  history = ((history << 1) | (uint32_t)taken) & ((1u << BP_BITS) - 1);
  cbr_executed++;
}
static void executed(block_t *b) { b->executions++; }

/* v1 counts the starting 64-byte line of attempted near integer MOV loads into
 * a GPR. Exclude RSP/RBP-based addressing, implicit operands, RMW, LEA, SIMD
 * and string instructions. Static, single-threaded x86-64 fixtures only. */
static bool load_pattern;
static hashtable_t load_sites;
static volatile int threads_started;
static uint64_t first, same_line, adjacent_line, nonadjacent_line;

static void feature_error(const char *reason) {
  dr_fprintf(STDERR, "%s: %s\n", recurrence_only ? "loop_recurrence_only" : "load_pattern", reason);
  dr_abort_with_code(1);
}

static void thread_started(void *dc) {
  (void)dc;
  if (dr_atomic_add32_return_sum(&threads_started, 1) != 1)
    feature_error("requires a single application thread");
}

typedef struct {
  opnd_t operand;
  uintptr_t previous_line;
} load_site_t;

static void free_site(void *site) {
  dr_global_free(site, sizeof(load_site_t));
}

static void record_load(load_site_t *site) {
  dr_mcontext_t mc;
  mc.size = sizeof(mc);
  mc.flags = DR_MC_INTEGER | DR_MC_CONTROL;
  if (!dr_get_mcontext(dr_get_current_drcontext(), &mc))
    feature_error("application address context unavailable");
  uintptr_t line = (uintptr_t)opnd_compute_address(site->operand, &mc) >> 6;
  uintptr_t previous = site->previous_line;
  uint64_t *counter;
  if (previous == UINTPTR_MAX) counter = &first;
  else if (line == previous) counter = &same_line;
  else if (line == previous + 1 || previous == line + 1) counter = &adjacent_line;
  else counter = &nonadjacent_line;
  if (*counter == UINT64_MAX) feature_error("counter overflow");
  ++*counter;
  site->previous_line = line;
}

static void instrument_load(void *dc, instrlist_t *bb, instr_t *where) {
  int op = instr_get_opcode(where);
  if (op != OP_mov_ld && op != OP_movsx && op != OP_movsxd && op != OP_movzx) return;
  if (instr_num_srcs(where) != 1 || instr_num_dsts(where) != 1) return;
  opnd_t mem = instr_get_src(where, 0), dst = instr_get_dst(where, 0);
  if (!opnd_is_near_memory_reference(mem) || !opnd_is_reg(dst) ||
      !reg_is_gpr(opnd_get_reg(dst)) || opnd_uses_reg(mem, DR_REG_XSP) ||
      opnd_uses_reg(mem, DR_REG_XBP)) return;

  /* Different translations of one instruction share the same previous line. */
  app_pc pc = instr_get_app_pc(where);
  if (pc == NULL) feature_error("missing application PC");
  dr_mutex_lock(lock);
  load_site_t *site = hashtable_lookup(&load_sites, pc);
  if (!site) {
    site = dr_global_alloc(sizeof(*site));
    site->operand = mem;
    site->previous_line = UINTPTR_MAX;
    hashtable_add(&load_sites, pc, site);
  }
  dr_mutex_unlock(lock);

  /* The judge emulates CPUID using an application-context clean call in the
   * app2app phase. Register management via drreg is incompatible with that
   * callback in DynamoRIO 11.3. Read the unmodified address registers here. */
  dr_insert_clean_call_ex(dc, bb, where, (void *)record_load, DR_CLEANCALL_READS_APP_CONTEXT,
                          1, OPND_CREATE_INTPTR(site));
}

/* Crude static latency model (cycles), deliberately generic rather than tuned
 * to one processor: ALU 1, integer multiply 3, divide 20, FP add/multiply 4,
 * FP divide 14. A memory source is ready 4 cycles after its address registers,
 * so a load whose address is off the dependency chain is issued early, while
 * pointer chasing accumulates. Only register and flag dependencies are
 * tracked; memory dependencies, ports and the front end are ignored. */
#define REG_SLOTS 1024
#define FLAGS_SLOT (REG_SLOTS - 1)
static int latency(instr_t *ins) {
  int op = instr_get_opcode(ins), cycles = 1;
  switch (op) {
  case OP_imul: case OP_mul: case OP_mulx: cycles = 3; break;
  case OP_div: case OP_idiv: cycles = 20; break;
  case OP_addsd: case OP_subsd: case OP_mulsd: case OP_addss: case OP_subss: case OP_mulss:
  case OP_addpd: case OP_subpd: case OP_mulpd: case OP_addps: case OP_subps: case OP_mulps:
  case OP_cvtsi2sd: case OP_cvtsd2si: case OP_cvttsd2si: case OP_cvtsi2ss: cycles = 4; break;
  case OP_divsd: case OP_divss: case OP_sqrtsd: case OP_sqrtss: cycles = 14; break;
  default: if (instr_is_floating(ins)) cycles = 4; break;
  }
  return cycles;
}
static int slot(reg_id_t reg) {
  if (reg == DR_REG_NULL) return -1;
  if (reg_is_gpr(reg)) reg = reg_to_pointer_sized(reg);
  return reg < FLAGS_SLOT ? (int)reg : -1;
}
static void sources(instr_t *ins, int *ready, int *start) {
  int op = instr_get_opcode(ins);
  if (op == OP_nop_modrm) return; /* Its addressing operand is unused. */
  for (int i = 0; i < instr_num_srcs(ins); i++) {
    opnd_t o = instr_get_src(ins, i);
    int operand_ready = 0;
    for (int r = 0; r < opnd_num_regs_used(o); r++) {
      int s = slot(opnd_get_reg_used(o, r));
      if (s >= 0 && ready[s] > operand_ready) operand_ready = ready[s];
    }
    /* LEA needs the address registers. Only a load waits for memory. */
    if (opnd_is_memory_reference(o) && op != OP_lea) operand_ready += 4;
    if (operand_ready > *start) *start = operand_ready;
  }
  for (int i = 0; i < instr_num_dsts(ins); i++) { /* address registers of stores */
    opnd_t o = instr_get_dst(ins, i);
    if (!opnd_is_memory_reference(o)) continue;
    for (int r = 0; r < opnd_num_regs_used(o); r++) {
      int s = slot(opnd_get_reg_used(o, r));
      if (s >= 0 && ready[s] > *start) *start = ready[s];
    }
  }
}
/* One pass over the block with persistent ready times; returns the latest finish. */
static int pass(instrlist_t *bb, int *ready, int finish) {
  for (instr_t *ins = instrlist_first_app(bb); ins; ins = instr_get_next_app(ins)) {
    int start = 0;
    uint flags = instr_get_eflags(ins, DR_QUERY_DEFAULT);
    sources(ins, ready, &start);
    if ((flags & EFLAGS_READ_ALL) && ready[FLAGS_SLOT] > start) start = ready[FLAGS_SLOT];
    int done = start + latency(ins);
    for (int i = 0; i < instr_num_dsts(ins); i++) {
      opnd_t o = instr_get_dst(ins, i);
      if (opnd_is_reg(o)) { int s = slot(opnd_get_reg(o)); if (s >= 0) ready[s] = done; }
    }
    if (flags & EFLAGS_WRITE_ALL) ready[FLAGS_SLOT] = done;
    if (done > finish) finish = done;
  }
  return finish;
}
static bool self_loop(void *tag, instrlist_t *bb) {
  instr_t *last = instrlist_last_app(bb);
  return last && instr_is_cbr(last) && opnd_is_pc(instr_get_target(last)) &&
         opnd_get_pc(instr_get_target(last)) == dr_fragment_app_pc(tag);
}

/* The caller has already completed the first pass in ready. Measure the
 * growth from four to eight passes using the full model's dependency rules. */
static uint32_t recurrence(instrlist_t *bb, int *ready) {
  int t4 = 0, t8 = 0;
  for (int i = 2; i <= 8; ++i) {
    int finish = pass(bb, ready, 0);
    if (i == 4) t4 = finish;
    if (i == 8) t8 = finish;
  }
  return (uint32_t)((t8 - t4) * 16 / 4);
}

static void charge_recurrence(ptr_uint_t weight) {
  if (UINT64_MAX - recurrence_total < weight)
    feature_error("counter overflow");
  recurrence_total += weight;
}

static void reject_fork(void *dc) {
  (void)dc;
  feature_error("requires a single process");
}

static void latency_features(void *tag, instrlist_t *bb, block_t *b) {
  int ready[REG_SLOTS] = {0};
  b->f[F_CRITICAL_X16] = (uint32_t)pass(bb, ready, 0) * 16;
  if (self_loop(tag, bb))
    b->f[F_RECURRENCE_X16] = recurrence(bb, ready);
  /* Base cost: a 4-wide machine's issue bound, or a loop's recurrence if longer. */
  uint32_t issue = b->f[F_INSTR] * 16 / 4;
  b->f[F_BOUND_X16] = b->f[F_RECURRENCE_X16] > issue ? b->f[F_RECURRENCE_X16] : issue;
}

static dr_emit_flags_t analyze(void *dc, void *tag, instrlist_t *bb, bool for_trace, bool translating, void **user_data) {
  (void)for_trace; (void)translating;
  if (recurrence_only) {
    *user_data = NULL;
    if (self_loop(tag, bb)) {
      int ready[REG_SLOTS] = {0};
      pass(bb, ready, 0);
      *user_data = (void *)(ptr_uint_t)recurrence(bb, ready);
    }
    return DR_EMIT_DEFAULT;
  }
  block_t *b = dr_global_alloc(sizeof(*b));
  memset(b, 0, sizeof(*b));
  for (instr_t *ins = instrlist_first_app(bb); ins; ins = instr_get_next_app(ins)) {
    int op = instr_get_opcode(ins);
    b->f[F_INSTR]++;
    if (instr_reads_memory(ins)) b->f[F_LOAD]++;
    if (instr_writes_memory(ins)) b->f[F_STORE]++;
    if (instr_is_cbr(ins)) b->f[F_CBR]++;
    else if (instr_is_ubr(ins) || instr_is_call_direct(ins)) b->f[F_UBR]++;
    else if (instr_is_mbr(ins)) b->f[F_MBR]++;
    if (op == OP_imul || op == OP_mul || op == OP_mulx) b->f[F_MUL]++;
    if (op == OP_div || op == OP_idiv) b->f[F_DIV]++;
    if (instr_is_floating(ins)) b->f[F_FP]++;
  }
  (void)dc;
  latency_features(tag, bb, b);
  dr_mutex_lock(lock); b->next = blocks; blocks = b; dr_mutex_unlock(lock);
  *user_data = b;
  return DR_EMIT_DEFAULT;
}
static dr_emit_flags_t insert(void *dc, void *tag, instrlist_t *bb, instr_t *where, bool for_trace, bool translating, void *user_data) {
  (void)tag; (void)for_trace; (void)translating;
  if (recurrence_only) {
    if (user_data && drmgr_is_first_instr(dc, where))
      dr_insert_clean_call(dc, bb, where, (void *)charge_recurrence, false, 1,
                           OPND_CREATE_INTPTR(user_data));
    return DR_EMIT_DEFAULT;
  }
  if (drmgr_is_first_instr(dc, where))
    dr_insert_clean_call(dc, bb, where, (void *)executed, false, 1, OPND_CREATE_INTPTR(user_data));
  if (load_pattern && instr_is_app(where)) instrument_load(dc, bb, where);
  if (bpred && instr_is_app(where) && instr_is_cbr(where))
    dr_insert_cbr_instrumentation(dc, bb, where, (void *)on_cbr);
  return DR_EMIT_DEFAULT;
}
static void finish(void) {
  if (recurrence_only) {
    file_t f = dr_open_file(output, DR_FILE_WRITE_REQUIRE_NEW);
    if (f == INVALID_FILE)
      feature_error("unable to create output file");
    dr_fprintf(f, "{\"version\":1,\"loop_recurrence_cycles_x16\":%llu}\n",
               (unsigned long long)recurrence_total);
    dr_close_file(f);
    return;
  }
  uint64_t totals[F_COUNT] = {0};
  for (block_t *b = blocks; b; b = b->next)
    for (int i = 0; i < F_COUNT; i++) totals[i] += b->executions * b->f[i];
  file_t f = dr_open_file(output, DR_FILE_WRITE_REQUIRE_NEW);
  dr_fprintf(f, "{");
  for (int i = 0; i < F_COUNT; i++) dr_fprintf(f, "\"%s\":%llu,", names[i], (unsigned long long)totals[i]);
  if (load_pattern) {
    dr_fprintf(f, "\"load_pattern\":{\"version\":1,\"first\":%llu,\"same_line\":%llu,"
               "\"adjacent_line\":%llu,\"nonadjacent_line\":%llu},",
               (unsigned long long)first, (unsigned long long)same_line,
               (unsigned long long)adjacent_line, (unsigned long long)nonadjacent_line);
    hashtable_delete(&load_sites);
  }
  dr_fprintf(f, "\"bpred\":%s,\"cbr_executed\":%llu,\"mispredicts\":%llu}\n", bpred ? "true" : "false",
             (unsigned long long)cbr_executed, (unsigned long long)mispredicts);
  dr_close_file(f);
}
DR_EXPORT void dr_client_main(client_id_t id, int argc, const char *argv[]) {
  (void)id;
  for (int i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "-out") && i + 1 < argc) { strncpy(output, argv[++i], sizeof(output) - 1); }
    else if (!strcmp(argv[i], "-loop_recurrence_only")) recurrence_only = true;
    else if (!strcmp(argv[i], "-load_pattern")) load_pattern = true;
    else if (!strcmp(argv[i], "-bpred")) bpred = true;
  }
  if (recurrence_only) {
    if (load_pattern || bpred || !output[0])
      feature_error("requires -out and excludes -bpred and -load_pattern");
    if (dr_durante_runtime_abi_version == NULL ||
        dr_durante_runtime_abi_version() != DR_DURANTE_RUNTIME_ABI_VERSION)
      feature_error("requires the prepared Durante runtime");
    uint64 ignore_eflags;
    if (!dr_get_integer_option("cleancall_ignore_eflags", &ignore_eflags) || ignore_eflags)
      feature_error("requires full clean-call flag preservation");
  }
  if (!drmgr_init())
    feature_error("cannot initialize instrumentation");
  if (!recurrence_only) lock = dr_mutex_create();
  if ((load_pattern || recurrence_only) && !drmgr_register_thread_init_event(thread_started))
    feature_error("cannot initialize thread check");
  if (recurrence_only)
    dr_register_fork_init_event(reject_fork);
  if (load_pattern)
    hashtable_init_ex(&load_sites, 8, HASH_INTPTR, false, false, free_site, NULL, NULL);
  if (!drmgr_register_bb_instrumentation_event(analyze, insert, NULL))
    feature_error("cannot initialize instrumentation");
  dr_register_exit_event(finish);
}

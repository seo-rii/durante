/* Exploratory feature client: per-BB static instruction classes counted at BB
 * entry, and optionally a gshare branch-predictor simulation. Single-threaded
 * workloads only; not a production meter. */
#include "dr_api.h"
#include "drmgr.h"
#include "drreg.h"
#include "drutil.h"
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
static bool bpred;
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

static void pattern_error(const char *reason) {
  dr_fprintf(STDERR, "load_pattern: %s\n", reason);
  dr_abort_with_code(1);
}

static void thread_started(void *dc) {
  (void)dc;
  if (dr_atomic_add32_return_sum(&threads_started, 1) != 1)
    pattern_error("requires a single application thread");
}

static void free_site(void *previous_line) {
  dr_global_free(previous_line, sizeof(uintptr_t));
}

static void record_load(uintptr_t *previous_line, uintptr_t address) {
  uintptr_t line = address >> 6, previous = *previous_line;
  uint64_t *counter;
  if (previous == UINTPTR_MAX) counter = &first;
  else if (line == previous) counter = &same_line;
  else if (line == previous + 1 || previous == line + 1) counter = &adjacent_line;
  else counter = &nonadjacent_line;
  if (*counter == UINT64_MAX) pattern_error("counter overflow");
  ++*counter;
  *previous_line = line;
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
  if (pc == NULL) pattern_error("missing application PC");
  dr_mutex_lock(lock);
  uintptr_t *previous = hashtable_lookup(&load_sites, pc);
  if (!previous) {
    previous = dr_global_alloc(sizeof(*previous));
    *previous = UINTPTR_MAX;
    hashtable_add(&load_sites, pc, previous);
  }
  dr_mutex_unlock(lock);

  reg_id_t addr;
  /* drutil needs no second scratch register for these near x86 operands. */
  if (drreg_reserve_register(dc, bb, where, NULL, &addr) != DRREG_SUCCESS ||
      drreg_restore_app_values(dc, bb, where, mem, NULL) != DRREG_SUCCESS ||
      !drutil_insert_get_mem_addr(dc, bb, where, mem, addr, addr))
    pattern_error("cannot compute application load address");
  dr_insert_clean_call(dc, bb, where, (void *)record_load, false, 2,
                       OPND_CREATE_INTPTR(previous), opnd_create_reg(addr));
  if (drreg_unreserve_register(dc, bb, where, addr) != DRREG_SUCCESS)
    pattern_error("cannot restore application register");
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
static void latency_features(void *tag, instrlist_t *bb, block_t *b) {
  static int ready[REG_SLOTS];
  memset(ready, 0, sizeof(ready));
  b->f[F_CRITICAL_X16] = (uint32_t)pass(bb, ready, 0) * 16;
  instr_t *last = instrlist_last_app(bb);
  if (last && instr_is_cbr(last) && opnd_is_pc(instr_get_target(last)) &&
      opnd_get_pc(instr_get_target(last)) == dr_fragment_app_pc(tag)) {
    /* Self-loop: steady-state growth of the critical path per iteration. */
    memset(ready, 0, sizeof(ready));
    int t4 = 0, t8 = 0;
    for (int i = 0; i < 8; i++) { int t = pass(bb, ready, 0); if (i == 3) t4 = t; if (i == 7) t8 = t; }
    b->f[F_RECURRENCE_X16] = (uint32_t)((t8 - t4) * 16 / 4);
  }
  /* Base cost: a 4-wide machine's issue bound, or a loop's recurrence if longer. */
  uint32_t issue = b->f[F_INSTR] * 16 / 4;
  b->f[F_BOUND_X16] = b->f[F_RECURRENCE_X16] > issue ? b->f[F_RECURRENCE_X16] : issue;
}

static dr_emit_flags_t analyze(void *dc, void *tag, instrlist_t *bb, bool for_trace, bool translating, void **user_data) {
  (void)for_trace; (void)translating;
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
  dr_mutex_lock(lock);
  latency_features(tag, bb, b);
  dr_mutex_unlock(lock);
  dr_mutex_lock(lock); b->next = blocks; blocks = b; dr_mutex_unlock(lock);
  *user_data = b;
  return DR_EMIT_DEFAULT;
}
static dr_emit_flags_t insert(void *dc, void *tag, instrlist_t *bb, instr_t *where, bool for_trace, bool translating, void *user_data) {
  (void)tag; (void)for_trace; (void)translating;
  if (drmgr_is_first_instr(dc, where))
    dr_insert_clean_call(dc, bb, where, (void *)executed, false, 1, OPND_CREATE_INTPTR(user_data));
  if (load_pattern && instr_is_app(where)) instrument_load(dc, bb, where);
  if (bpred && instr_is_app(where) && instr_is_cbr(where))
    dr_insert_cbr_instrumentation(dc, bb, where, (void *)on_cbr);
  return DR_EMIT_DEFAULT;
}
static void finish(void) {
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
    drreg_exit();
    drutil_exit();
  }
  dr_fprintf(f, "\"bpred\":%s,\"cbr_executed\":%llu,\"mispredicts\":%llu}\n", bpred ? "true" : "false",
             (unsigned long long)cbr_executed, (unsigned long long)mispredicts);
  dr_close_file(f);
}
DR_EXPORT void dr_client_main(client_id_t id, int argc, const char *argv[]) {
  (void)id;
  for (int i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "-out") && i + 1 < argc) { strncpy(output, argv[++i], sizeof(output) - 1); }
    else if (!strcmp(argv[i], "-load_pattern")) load_pattern = true;
    else if (!strcmp(argv[i], "-bpred")) bpred = true;
  }
  drmgr_init();
  lock = dr_mutex_create();
  if (load_pattern) {
    drreg_options_t opts = { .struct_size = sizeof(opts), .num_spill_slots = 2,
                             .conservative = true };
    if (!drutil_init() || drreg_init(&opts) != DRREG_SUCCESS ||
        !drmgr_register_thread_init_event(thread_started))
      pattern_error("cannot initialize instrumentation");
    hashtable_init_ex(&load_sites, 8, HASH_INTPTR, false, false, free_site, NULL, NULL);
  }
  drmgr_register_bb_instrumentation_event(analyze, insert, NULL);
  dr_register_exit_event(finish);
}

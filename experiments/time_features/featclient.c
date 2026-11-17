/* Exploratory feature client: per-BB static instruction classes counted at BB
 * entry, and optionally a gshare branch-predictor simulation. Single-threaded
 * workloads only; not a production meter. */
#include "dr_api.h"
#include "drmgr.h"
#include <stdint.h>
#include <string.h>

enum { F_INSTR, F_LOAD, F_STORE, F_CBR, F_UBR, F_MBR, F_MUL, F_DIV, F_FP, F_COUNT };
static const char *names[F_COUNT] = {"instructions", "loads", "stores", "cond_branches", "direct_jumps_calls", "indirect_branches", "muls", "divs", "fp_ops"};
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

static dr_emit_flags_t analyze(void *dc, void *tag, instrlist_t *bb, bool for_trace, bool translating, void **user_data) {
  (void)tag; (void)for_trace; (void)translating;
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
  dr_mutex_lock(lock); b->next = blocks; blocks = b; dr_mutex_unlock(lock);
  *user_data = b;
  return DR_EMIT_DEFAULT;
}
static dr_emit_flags_t insert(void *dc, void *tag, instrlist_t *bb, instr_t *where, bool for_trace, bool translating, void *user_data) {
  (void)tag; (void)for_trace; (void)translating;
  if (drmgr_is_first_instr(dc, where))
    dr_insert_clean_call(dc, bb, where, (void *)executed, false, 1, OPND_CREATE_INTPTR(user_data));
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
  dr_fprintf(f, "\"bpred\":%s,\"cbr_executed\":%llu,\"mispredicts\":%llu}\n", bpred ? "true" : "false",
             (unsigned long long)cbr_executed, (unsigned long long)mispredicts);
  dr_close_file(f);
}
DR_EXPORT void dr_client_main(client_id_t id, int argc, const char *argv[]) {
  (void)id;
  for (int i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "-out") && i + 1 < argc) { strncpy(output, argv[++i], sizeof(output) - 1); }
    else if (!strcmp(argv[i], "-bpred")) bpred = true;
  }
  drmgr_init();
  lock = dr_mutex_create();
  drmgr_register_bb_instrumentation_event(analyze, insert, NULL);
  dr_register_exit_event(finish);
}

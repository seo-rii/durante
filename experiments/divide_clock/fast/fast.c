/* Research-only build of the ordinary block meter. The experimental bound
 * replaces one translation-time constant. Its independent capability marker
 * makes the ordinary reader reject the resulting stream. */
#include "dr_api.h"
#include "drmgr.h"
#include <string.h>
#include "latency_bound.h"
#undef DURANTE_LATENCY_BOUND_VERSION
#define DURANTE_LATENCY_BOUND_VERSION 9001
#define DURANTE_TEST_BLOCK_LATENCY_BOUND divide_bound
#define dr_client_main divide_base_main
#include "../../../client/durante.c"
#undef dr_client_main

/* Frozen in the historical control fit (../parameters.json). */
enum { DIV_LATENCY = 9, DIV_INTERVAL = 6 };
static app_pc divide_module_start, divide_module_end;

static bool divide32(instr_t *ins) {
  return instr_get_opcode(ins) == OP_div && opnd_get_size(instr_get_src(ins, 0)) == OPSZ_4;
}

static bool independent_zero(instr_t *ins) {
  if (instr_get_opcode(ins) != OP_xor || instr_num_srcs(ins) != 2 || instr_num_dsts(ins) != 1)
    return false;
  opnd_t a = instr_get_src(ins, 0);
  opnd_size_t size = opnd_get_size(a);
  return opnd_is_reg(a) && reg_is_gpr(opnd_get_reg(a)) && (size == OPSZ_4 || size == OPSZ_8) &&
         opnd_same(a, instr_get_src(ins, 1)) && opnd_same(a, instr_get_dst(ins, 0));
}

static uint32_t divide_pass(instrlist_t *bb, uint32_t *ready) {
  uint32_t finish = 0;
  for (instr_t *ins = instrlist_first_app(bb); ins; ins = instr_get_next_app(ins)) {
    uint flags = instr_get_eflags(ins, DR_QUERY_DEFAULT);
    uint32_t start = independent_zero(ins) ? 0 : durante_latency_start(ins, ready);
    if ((flags & EFLAGS_READ_ALL) && ready[DURANTE_LATENCY_FLAGS_SLOT] > start)
      start = ready[DURANTE_LATENCY_FLAGS_SLOT];
    uint32_t done = start + (divide32(ins) ? DIV_LATENCY : (uint32_t)durante_latency_cycles(ins));
    for (int i = 0; i < instr_num_dsts(ins); ++i) {
      opnd_t operand = instr_get_dst(ins, i);
      if (opnd_is_reg(operand)) {
        int slot = durante_latency_slot(opnd_get_reg(operand));
        if (slot >= 0)
          ready[slot] = done;
      }
    }
    if (flags & EFLAGS_WRITE_ALL)
      ready[DURANTE_LATENCY_FLAGS_SLOT] = done;
    if (done > finish)
      finish = done;
  }
  return finish;
}

static uint32_t divide_bound(void *ctx, void *tag, instrlist_t *bb, uint instructions) {
  if (instructions == 0 || instructions > (1u << 16))
    return UINT32_MAX;
  uint divides = 0;
  app_pc pc = dr_fragment_app_pc(tag);
  if (pc >= divide_module_start && pc < divide_module_end) {
    for (instr_t *ins = instrlist_first_app(bb); ins; ins = instr_get_next_app(ins))
      divides += divide32(ins);
  }
  if (!divides)
    return durante_latency_bound_x16(ctx, tag, bb, instructions);
  /* A block containing an emulated instruction has no matching replay
   * descriptor. Propagate an accounting error on the same scope boundary. */
  for (instr_t *ins = instrlist_first(bb); ins; ins = instr_get_next(ins))
    if (drmgr_is_emulation_start(ins) || drmgr_is_emulation_end(ins))
      return UINT32_MAX;
  uint32_t cost = instructions * 4;
  uint32_t divider_floor = divides * 16 * DIV_INTERVAL;
  if (cost < divider_floor)
    cost = divider_floor;
  instr_t *last = instrlist_last_app(bb);
  if (last && instr_is_cbr(last) && opnd_is_pc(instr_get_target(last)) && opnd_get_pc(instr_get_target(last)) == pc) {
    uint32_t *ready = dr_thread_alloc(ctx, sizeof(*ready) * DURANTE_LATENCY_SLOTS);
    if (!ready)
      return UINT32_MAX;
    memset(ready, 0, sizeof(*ready) * DURANTE_LATENCY_SLOTS);
    uint32_t fourth = 0, final = 0;
    for (int i = 0; i < 8; ++i) {
      final = divide_pass(bb, ready);
      if (i == 3)
        fourth = final;
    }
    dr_thread_free(ctx, ready, sizeof(*ready) * DURANTE_LATENCY_SLOTS);
    uint32_t recurrence = final > fourth ? 4 * (final - fourth) : 0;
    if (cost < recurrence)
      cost = recurrence;
  }
  return cost;
}

DR_EXPORT void dr_client_main(client_id_t id, int argc, const char *argv[]) {
  if (argc != 8 || strcmp(argv[1], "-outdir") || strcmp(argv[3], "-rep_bytes") ||
      strcmp(argv[4], "-cpuid_profile") || strcmp(argv[5], "x86-64-v2") ||
      strcmp(argv[6], "-latency_bound") || strcmp(argv[7], "-memory_operand_bytes"))
    fail("expected the divide study diagnostic arguments");
  module_data_t *main = dr_get_main_module();
  if (!main)
    fail("missing main module");
  divide_module_start = main->start;
  divide_module_end = main->end;
  dr_free_module_data(main);
  divide_base_main(id, argc, argv);
}

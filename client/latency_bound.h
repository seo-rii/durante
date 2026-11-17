/* Static per-basic-block latency bound, version 1. Include dr_api.h and
 * drmgr.h first.
 *
 * Each block gets one constant, in sixteenths of a cycle, computed from its
 * instructions alone; the counter charges it at block entry, exactly like the
 * instruction count. It is a deliberately generic machine model, not a
 * prediction for any real processor:
 *
 *   - latency: integer multiply 3 cycles, integer divide 20, scalar FP
 *     divide and square root 14, listed FP add/subtract/multiply/convert and
 *     any other instruction DynamoRIO classifies as floating point 4, all
 *     others 1;
 *   - a memory source is ready 4 cycles after its address registers, so an
 *     independent load issues early while pointer chasing accumulates;
 *   - only register and flag dependencies are tracked; memory
 *     dependencies, ports, caches, branch prediction and the front end are not.
 *
 * bound = max(issue, recurrence), where issue = instructions / 4 (a 4-wide
 * machine) and recurrence is the steady-state growth of the critical path per
 * iteration of a block whose final conditional branch targets its own start
 * (measured between the 4th and 8th of eight back-to-back passes). Other
 * blocks have no recurrence. An emulation region (for example a virtualized
 * CPUID) counts as one instruction and contributes its single application
 * placeholder, which has latency 1 and no operands.
 *
 * Applications can still change their own counters, so this is diagnostic
 * input to a time model like the instruction count, never a trusted bound. */
#ifndef DURANTE_LATENCY_BOUND_H
#define DURANTE_LATENCY_BOUND_H

#include <stdint.h>

#define DURANTE_LATENCY_BOUND_VERSION 1
#define DURANTE_LATENCY_SLOTS 1024
#define DURANTE_LATENCY_FLAGS_SLOT (DURANTE_LATENCY_SLOTS - 1)

static inline int durante_latency_cycles(instr_t *ins) {
  switch (instr_get_opcode(ins)) {
  case OP_imul:
  case OP_mul:
  case OP_mulx:
    return 3;
  case OP_div:
  case OP_idiv:
    return 20;
  case OP_divsd:
  case OP_divss:
  case OP_sqrtsd:
  case OP_sqrtss:
    return 14;
  case OP_addsd: case OP_subsd: case OP_mulsd: case OP_addss: case OP_subss:
  case OP_mulss: case OP_addpd: case OP_subpd: case OP_mulpd: case OP_addps:
  case OP_subps: case OP_mulps: case OP_cvtsi2sd: case OP_cvtsd2si:
  case OP_cvttsd2si: case OP_cvtsi2ss:
    return 4;
  default:
    return instr_is_floating(ins) ? 4 : 1;
  }
}

/* General-purpose subregisters share their full register's slot. */
static inline int durante_latency_slot(reg_id_t reg) {
  if (reg == DR_REG_NULL)
    return -1;
  if (reg_is_gpr(reg))
    reg = reg_to_pointer_sized(reg);
  return reg < DURANTE_LATENCY_FLAGS_SLOT ? (int)reg : -1;
}

static inline uint32_t durante_latency_start(instr_t *ins, const uint32_t *ready) {
  uint32_t start = 0;
  int op = instr_get_opcode(ins);
  bool address_only = op == OP_lea || op == OP_nop_modrm;
  for (int i = 0; i < instr_num_srcs(ins); ++i) {
    opnd_t o = instr_get_src(ins, i);
    bool memory = opnd_is_memory_reference(o);
    uint32_t address = 0;
    for (int r = 0; r < opnd_num_regs_used(o); ++r) {
      int s = durante_latency_slot(opnd_get_reg_used(o, r));
      if (s < 0)
        continue;
      if (memory) {
        if (ready[s] > address)
          address = ready[s];
      } else if (ready[s] > start) {
        start = ready[s];
      }
    }
    /* Loaded data arrives a fixed latency after its address is known. */
    if (memory && !address_only && address + 4 > start)
      start = address + 4;
  }
  /* A store cannot issue before its address registers are ready. */
  for (int i = 0; i < instr_num_dsts(ins); ++i) {
    opnd_t o = instr_get_dst(ins, i);
    if (!opnd_is_memory_reference(o))
      continue;
    for (int r = 0; r < opnd_num_regs_used(o); ++r) {
      int s = durante_latency_slot(opnd_get_reg_used(o, r));
      if (s >= 0 && ready[s] > start)
        start = ready[s];
    }
  }
  return start;
}

/* One pass over the block's application instructions with persistent ready
 * times. Returns the latest finish time. */
static inline uint32_t durante_latency_pass(instrlist_t *bb, uint32_t *ready) {
  uint32_t finish = 0;
  for (instr_t *ins = instrlist_first_app(bb); ins; ins = instr_get_next_app(ins)) {
    uint flags = instr_get_eflags(ins, DR_QUERY_DEFAULT);
    uint32_t start = durante_latency_start(ins, ready);
    if ((flags & EFLAGS_READ_ALL) && ready[DURANTE_LATENCY_FLAGS_SLOT] > start)
      start = ready[DURANTE_LATENCY_FLAGS_SLOT];
    uint32_t done = start + (uint32_t)durante_latency_cycles(ins);
    for (int i = 0; i < instr_num_dsts(ins); ++i) {
      opnd_t o = instr_get_dst(ins, i);
      if (opnd_is_reg(o)) {
        int s = durante_latency_slot(opnd_get_reg(o));
        if (s >= 0)
          ready[s] = done;
      }
    }
    if (flags & EFLAGS_WRITE_ALL)
      ready[DURANTE_LATENCY_FLAGS_SLOT] = done;
    if (done > finish)
      finish = done;
  }
  return finish;
}

/* The block's bound in sixteenths of a cycle. instructions is the block's
 * logical instruction count (an emulation region counts once). Returns
 * UINT32_MAX when the block cannot be analyzed, which the caller must treat as
 * an accounting failure. Deterministic for identical lists, so translation
 * passes reproduce it. */
static inline uint32_t durante_latency_bound_x16(void *drcontext, void *tag,
                                          instrlist_t *bb, uint instructions) {
  if (instructions == 0 || instructions > (1u << 16))
    return UINT32_MAX;
  uint32_t issue = instructions * 4; /* instructions * 16 / 4 */
  uint32_t recurrence = 0;
  instr_t *last = instrlist_last_app(bb);
  if (last && instr_is_cbr(last) && opnd_is_pc(instr_get_target(last)) &&
      opnd_get_pc(instr_get_target(last)) == dr_fragment_app_pc(tag)) {
    uint32_t *ready = dr_thread_alloc(drcontext, sizeof(uint32_t) * DURANTE_LATENCY_SLOTS);
    if (!ready)
      return UINT32_MAX;
    memset(ready, 0, sizeof(uint32_t) * DURANTE_LATENCY_SLOTS);
    uint32_t t4 = 0, t8 = 0;
    for (int i = 0; i < 8; ++i) {
      uint32_t t = durante_latency_pass(bb, ready);
      if (i == 3)
        t4 = t;
      else if (i == 7)
        t8 = t;
    }
    dr_thread_free(drcontext, ready, sizeof(uint32_t) * DURANTE_LATENCY_SLOTS);
    /* (t8 - t4) cycles over four iterations, in sixteenths: * 16 / 4. */
    recurrence = t8 > t4 ? (t8 - t4) * 4 : 0;
  }
  return recurrence > issue ? recurrence : issue;
}

#endif

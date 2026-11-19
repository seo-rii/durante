#ifndef DURANTE_EXPERIMENTS_L1D_PROBE_H
#define DURANTE_EXPERIMENTS_L1D_PROBE_H

/* The scalar clean calls omit SIMD saves. Constrain both their bodies and
 * every cache helper they can call when the compiler chooses not to inline. */
#ifdef __clang__
#pragma clang attribute push(__attribute__((target("general-regs-only"))), apply_to=function)
#else
#pragma GCC push_options
#pragma GCC target("general-regs-only")
#endif
#include "cache.h"

static l1d_cache_t probe_cache;
static uint64_t probe_skipped, probe_rep;
static int probe_threads;
static uint64_t probe_read_refs;
static unsigned probe_block_reads;
static instr_t *probe_block_last;
static thread_data_t *probe_thread_data;
static bool probe_inline_block;

static uint write_suffix(instrlist_t *bb, instr_t *where);

/* Low byte holds index scale and flags, followed by width, then signed disp32. */
enum { PROBE_ADDR32 = 16, PROBE_TWICE = 32, PROBE_SIZE_SHIFT = 8 };

static void probe_skip(void) { ++probe_skipped; }

static void probe_access_data(uintptr_t base, uintptr_t index, uint64_t spec) {
  unsigned bytes = (uint32_t)spec >> PROBE_SIZE_SHIFT;
  if (!bytes) {
    probe_skipped += spec & PROBE_TWICE ? 2 : 1;
    return;
  }
  uintptr_t addr = base + index * (spec & 15) + (int32_t)(spec >> 32);
  if (spec & PROBE_ADDR32)
    addr = (uint32_t)addr;
  if (spec & PROBE_TWICE)
    l1d_access_twice(&probe_cache, addr, bytes);
  else
    l1d_access(&probe_cache, addr, bytes);
}

/* Scalar reads charge their first line and reference together, once per
 * basic block. Only a cross-line operand adds a second lookup here. */
static void probe_read_slow(uintptr_t addr, unsigned bytes) {
  probe_cache.lines += l1d_touch_operand(&probe_cache, addr, bytes);
}

static void probe_displaced_read(uintptr_t base, intptr_t displacement, unsigned bytes) {
  l1d_access(&probe_cache, base + displacement, bytes);
}

static void probe_xlat(uintptr_t base, uintptr_t ax, bool addr32) {
  uintptr_t addr = base + (uint8_t)ax;
  l1d_access(&probe_cache, addr32 ? (uint32_t)addr : addr, 1);
}

static void probe_scalar_write(uintptr_t base, uintptr_t index, uint64_t spec,
                               app_pc pc, app_pc next_pc, int suffix) {
  record_write_checkpoint(probe_thread_data, pc, next_pc, suffix);
  probe_access_data(base, index, spec);
}
#ifdef __clang__
#pragma clang attribute pop
#else
#pragma GCC pop_options
#endif

/* The byte offset of a set in the tag table is the set-index bits of
 * the original address. The first tag is one past the MRU line number. */
_Static_assert(L1D_LINE_BYTES == 64 && sizeof(probe_cache.tags[0]) == L1D_LINE_BYTES,
               "inline MRU lookup requires a 64-byte tag row per cache line index");
_Static_assert((L1D_SETS & (L1D_SETS - 1)) == 0, "set mask requires a power of two");

static void probe_read(void *ctx, instrlist_t *bb, instr_t *where, opnd_t mem,
                       unsigned bytes, bool addr32) {
  const reg_id_t addr = DR_REG_R10, scratch = DR_REG_R11;
  opnd_t address = opnd_create_reg(addr), temp = opnd_create_reg(scratch);
  instr_t *slow = INSTR_CREATE_label(ctx), *done = INSTR_CREATE_label(ctx);
  ++probe_block_reads;
  dr_save_reg(ctx, bb, where, addr, SPILL_SLOT_1);
  dr_save_reg(ctx, bb, where, scratch, SPILL_SLOT_2);

#define PROBE_INSERT(ins) instrlist_meta_preinsert(bb, where, (ins))
  if (opnd_is_base_disp(mem) &&
      (opnd_get_base(mem) != DR_REG_NULL || opnd_get_index(mem) != DR_REG_NULL)) {
    opnd_set_size(&mem, OPSZ_lea);
    PROBE_INSERT(INSTR_CREATE_lea(ctx, addr32 ? opnd_create_reg(DR_REG_R10D) : address, mem));
  } else {
    ptr_int_t value = opnd_is_base_disp(mem) ? (ptr_int_t)opnd_get_disp(mem)
                                             : (ptr_int_t)opnd_get_addr(mem);
    instrlist_insert_mov_immed_ptrsz(ctx, addr32 ? (ptr_int_t)(uint32_t)value : value,
                                    address, bb, where, NULL, NULL);
  }

  /* Save flags even for instructions with conditional flag writes. */
  dr_save_arith_flags(ctx, bb, where, SPILL_SLOT_4);
  opnd_t low_temp = opnd_create_reg(DR_REG_R11D);
  PROBE_INSERT(INSTR_CREATE_mov_ld(ctx, low_temp, opnd_create_reg(DR_REG_R10D)));
  PROBE_INSERT(INSTR_CREATE_and(ctx, low_temp,
                                OPND_CREATE_INT32((L1D_SETS - 1) * L1D_LINE_BYTES)));
  ptr_int_t tags = (ptr_int_t)&probe_cache.tags;
  if (tags == (ptr_int_t)(int32_t)tags) {
    opnd_t set = opnd_create_base_disp(scratch, DR_REG_NULL, 0, (int)tags, OPSZ_PTR);
    PROBE_INSERT(INSTR_CREATE_mov_ld(ctx, temp, set));
  } else {
    /* x86-64 indexed addressing accepts a signed 32-bit displacement. */
    dr_save_reg(ctx, bb, where, DR_REG_R12, SPILL_SLOT_3);
    instrlist_insert_mov_immed_ptrsz(ctx, tags, opnd_create_reg(DR_REG_R12),
                                    bb, where, NULL, NULL);
    opnd_t set = opnd_create_base_disp(DR_REG_R12, scratch, 1, 0, OPSZ_PTR);
    PROBE_INSERT(INSTR_CREATE_mov_ld(ctx, temp, set));
    dr_restore_reg(ctx, bb, where, DR_REG_R12, SPILL_SLOT_3);
  }

  /* A nonempty MRU line contains the entire operand iff its exclusive
   * end minus the operand address lies in [bytes, L1D_LINE_BYTES]. */
  PROBE_INSERT(INSTR_CREATE_test(ctx, temp, temp));
  PROBE_INSERT(INSTR_CREATE_jcc(ctx, OP_jz, opnd_create_instr(slow)));
  PROBE_INSERT(INSTR_CREATE_shl(ctx, temp, OPND_CREATE_INT8(6)));
  PROBE_INSERT(INSTR_CREATE_sub(ctx, temp, address));
  PROBE_INSERT(INSTR_CREATE_sub(ctx, temp, OPND_CREATE_INT8(bytes)));
  PROBE_INSERT(INSTR_CREATE_cmp(ctx, temp, OPND_CREATE_INT8(L1D_LINE_BYTES - bytes)));
  PROBE_INSERT(INSTR_CREATE_jcc(ctx, OP_jbe, opnd_create_instr(done)));
  PROBE_INSERT(slow);
  dr_insert_clean_call_ex(ctx, bb, where, (void *)probe_read_slow, DR_CLEANCALL_NOSAVE_XMM,
                          2, address, OPND_CREATE_INT32(bytes));
  PROBE_INSERT(done);
#undef PROBE_INSERT

  dr_restore_arith_flags(ctx, bb, where, SPILL_SLOT_4);
  dr_restore_reg(ctx, bb, where, scratch, SPILL_SLOT_2);
  dr_restore_reg(ctx, bb, where, addr, SPILL_SLOT_1);
}

static void probe_operand(void *ctx, instrlist_t *bb, instr_t *where, opnd_t mem,
                          bool twice, bool fused) {
  unsigned bytes = opnd_size_in_bytes(opnd_get_size(mem));
  opnd_t base_arg = OPND_CREATE_INTPTR(0), index_arg = OPND_CREATE_INTPTR(0);
  uint64_t spec = twice ? PROBE_TWICE : 0;
  bool supported = bytes && bytes <= 64 && !opnd_is_vsib(mem) &&
                   opnd_get_segment(mem) != DR_SEG_FS && opnd_get_segment(mem) != DR_SEG_GS;
  if (supported && opnd_is_base_disp(mem)) {
    reg_id_t base = opnd_get_base(mem), idx = opnd_get_index(mem);
    if ((base != DR_REG_NULL && !reg_is_gpr(base)) ||
        (idx != DR_REG_NULL && !reg_is_gpr(idx))) {
      supported = false;
    } else {
      if (base != DR_REG_NULL)
        base_arg = opnd_create_reg(reg_to_pointer_sized(base));
      if (idx != DR_REG_NULL)
        index_arg = opnd_create_reg(reg_to_pointer_sized(idx));
      if ((base != DR_REG_NULL && reg_get_size(base) == OPSZ_4) ||
          (idx != DR_REG_NULL && reg_get_size(idx) == OPSZ_4) ||
          opnd_is_disp_short_addr(mem))
        spec |= PROBE_ADDR32;
      if (idx == DR_REG_AL && instr_get_opcode(where) == OP_xlat) {
        dr_insert_clean_call_ex(ctx, bb, where, (void *)probe_xlat,
                                DR_CLEANCALL_NOSAVE_XMM, 3, base_arg, index_arg,
                                OPND_CREATE_INT32((spec & PROBE_ADDR32) != 0));
        return;
      }
      spec |= ((uint64_t)(uint32_t)opnd_get_disp(mem) << 32) | (opnd_get_scale(mem) & 15);
    }
  } else if (supported && (opnd_is_rel_addr(mem) || opnd_is_abs_addr(mem))) {
    base_arg = OPND_CREATE_INTPTR(opnd_get_addr(mem));
  } else {
    supported = false;
  }
  if (supported)
    spec |= bytes << PROBE_SIZE_SHIFT;
  if (fused) {
    app_pc pc = instr_get_app_pc(where);
    app_pc next = pc ? pc + instr_length(ctx, where) : NULL;
    dr_insert_clean_call_ex(ctx, bb, where, (void *)probe_scalar_write,
                            DR_CLEANCALL_NOSAVE_XMM, 6, base_arg, index_arg,
                            OPND_CREATE_INTPTR(spec), OPND_CREATE_INTPTR(pc),
                            OPND_CREATE_INTPTR(next), OPND_CREATE_INT32(write_suffix(bb, where)));
  } else if (supported && !twice && probe_inline_block) {
    probe_read(ctx, bb, where, mem, bytes, (spec & PROBE_ADDR32) != 0);
  } else if (supported && !twice && !(spec & PROBE_ADDR32) && !opnd_is_reg(index_arg)) {
    dr_insert_clean_call_ex(ctx, bb, where, (void *)probe_displaced_read,
                            DR_CLEANCALL_NOSAVE_XMM, 3, base_arg,
                            OPND_CREATE_INTPTR((ptr_int_t)(int32_t)(spec >> 32)),
                            OPND_CREATE_INT32(bytes));
  } else {
    dr_insert_clean_call_ex(ctx, bb, where, (void *)probe_access_data,
                            DR_CLEANCALL_NOSAVE_XMM, 3, base_arg, index_arg,
                            OPND_CREATE_INTPTR(spec));
  }
}

static void probe_thread_init(thread_data_t *data) {
  if (dr_get_process_id() != owner_pid ||
      dr_atomic_add32_return_sum(&probe_threads, 1) != 1)
    fail("cache probe requires a single application thread and process");
  probe_thread_data = data;
}

static void probe_string(int op, unsigned width, bool addr32) {
  dr_mcontext_t mc;
  mc.size = sizeof(mc);
  mc.flags = DR_MC_INTEGER | DR_MC_CONTROL;
  if (!dr_get_mcontext(dr_get_current_drcontext(), &mc))
    fail("cache probe needs application context");
  ++probe_rep;
  l1d_repeat(&probe_cache, op == OP_rep_movs, mc.xsi, mc.xdi, mc.xcx,
             width, (mc.xflags & (1u << 10)) != 0, addr32);
}

static void probe_instrument_string(void *ctx, instrlist_t *bb, instr_t *where,
                                    int op) {
  unsigned width = 0;
  bool addr32 = false, unsupported_segment = false;
  if (op == OP_rep_movs || op == OP_rep_stos) {
    for (int i = 0; i < instr_num_dsts(where); ++i) {
      opnd_t dst = instr_get_dst(where, i);
      if (opnd_is_memory_reference(dst))
        width = opnd_size_in_bytes(opnd_get_size(dst));
    }
    for (int i = 0; i < instr_num_srcs(where); ++i) {
      opnd_t src = instr_get_src(where, i);
      if (opnd_is_reg(src) && opnd_get_reg(src) == DR_REG_ECX)
        addr32 = true;
      if (opnd_is_memory_reference(src) &&
          (opnd_get_segment(src) == DR_SEG_FS || opnd_get_segment(src) == DR_SEG_GS))
        unsupported_segment = true;
    }
  }
  if (unsupported_segment || !(width == 1 || width == 2 || width == 4 || width == 8)) {
    dr_insert_clean_call_ex(ctx, bb, where, (void *)probe_skip, DR_CLEANCALL_NOSAVE_XMM, 0);
    return;
  }
  dr_insert_clean_call_ex(ctx, bb, where, (void *)probe_string,
                          DR_CLEANCALL_READS_APP_CONTEXT, 3,
                          OPND_CREATE_INT32(op), OPND_CREATE_INT32(width),
                          OPND_CREATE_INT32(addr32));
}

/* Return whether this instruction's store callback supplies before_write's
 * checkpoint. The last read may coincide with the first write (RMW). */
static bool probe_instrument_operands(void *ctx, instrlist_t *bb, instr_t *where,
                                      bool writes_memory) {
  if (!instr_is_app(where))
    return false;
  int op = instr_get_opcode(where);
  if (instr_is_rep_string_op(where)) {
    probe_instrument_string(ctx, bb, where, op);
    return false;
  }
  if (instr_is_prefetch(where) || op == OP_lea || op == OP_nop_modrm)
    return false;

  int first_write = -1;
  bool fused = false;
  if (writes_memory) {
    for (int i = 0; i < instr_num_dsts(where); ++i) {
      if (!opnd_is_memory_reference(instr_get_dst(where, i)))
        continue;
      if (first_write >= 0) {
        fused = false;
        break;
      }
      first_write = i;
      fused = true;
    }
  }

  /* Emit reads in order, holding the final operand until we know whether
   * the matching write can share its cache lookup. */
  opnd_t pending = opnd_create_null();
  if (instr_reads_memory(where)) {
    for (int i = 0; i < instr_num_srcs(where); ++i) {
      opnd_t src = instr_get_src(where, i);
      if (!opnd_is_memory_reference(src))
        continue;
      if (!opnd_is_null(pending))
        probe_operand(ctx, bb, where, pending, false, false);
      pending = src;
    }
  }
  bool twice = first_write >= 0 && opnd_same(pending, instr_get_dst(where, first_write));
  if (!twice && !opnd_is_null(pending))
    probe_operand(ctx, bb, where, pending, false, false);
  if (first_write >= 0) {
    for (int i = first_write; i < instr_num_dsts(where); ++i) {
      opnd_t dst = instr_get_dst(where, i);
      if (opnd_is_memory_reference(dst))
        probe_operand(ctx, bb, where, dst, twice && i == first_write, fused);
    }
  }
  return fused;
}

static bool probe_instrument(void *ctx, instrlist_t *bb, instr_t *where, bool writes_memory) {
  if (drmgr_is_first_instr(ctx, where)) {
    probe_block_reads = 0;
    probe_block_last = instrlist_last_app(bb);
  }
  bool fused = probe_instrument_operands(ctx, bb, where, writes_memory);
  /* Signals and partial-block replays suppress the diagnostic report.
   * Every reported block reached all of its instrumented operands. */
  if (where == probe_block_last && probe_block_reads) {
    opnd_t dst = opnd_create_rel_addr(&probe_read_refs, OPSZ_PTR);
    opnd_t reg = opnd_create_reg(DR_REG_R11);
    dr_save_reg(ctx, bb, where, DR_REG_R11, SPILL_SLOT_1);
    instrlist_meta_preinsert(bb, where, INSTR_CREATE_mov_ld(ctx, reg, dst));
    instrlist_meta_preinsert(bb, where, INSTR_CREATE_lea(ctx, reg,
        opnd_create_base_disp(DR_REG_R11, DR_REG_NULL, 0, probe_block_reads, OPSZ_lea)));
    instrlist_meta_preinsert(bb, where, INSTR_CREATE_mov_st(ctx, dst, reg));
    dr_restore_reg(ctx, bb, where, DR_REG_R11, SPILL_SLOT_1);
  }
  return fused;
}

static void probe_exit(void) {
  dr_fprintf(STDERR, "PROBE_L1D refs=%llu lines=%llu misses=%llu skipped=%llu rep=%llu threads=%d\n",
             (unsigned long long)(probe_cache.refs + probe_read_refs),
             (unsigned long long)(probe_cache.lines + probe_read_refs),
             (unsigned long long)probe_cache.misses, (unsigned long long)probe_skipped,
             (unsigned long long)probe_rep, probe_threads);
}

#endif

/* Host-side diagnostic L1 data cache on attempted application memory operands.
 * Single-threaded, static x86-64 programs only. No instruction fetch, prefetch,
 * system-call I/O, coherence or underlying CPU model. No address trace. */
static bool probe_l1d;
static int probe_threads;
static uint64_t probe_tags[64][8]; /* MRU first; zero is an invalid tag. */
static uint64_t probe_refs, probe_lines, probe_misses, probe_unsupported, probe_rep;

static void probe_line(uintptr_t line) {
  uint64_t tag = line + 1;
  uint64_t *set = probe_tags[line & 63];
  ++probe_lines;
  if (set[0] == tag)
    return;
  for (unsigned i = 1; i < 8; ++i) {
    if (set[i] == tag) {
      for (unsigned j = i; j; --j) set[j] = set[j - 1];
      set[0] = tag;
      return;
    }
  }
  ++probe_misses;
  for (unsigned j = 7; j; --j) set[j] = set[j - 1];
  set[0] = tag;
}
static void probe_ref(uintptr_t addr, unsigned bytes) {
  ++probe_refs;
  uintptr_t start = addr >> 6;
  uintptr_t last = (addr + bytes - 1) >> 6;
  do { probe_line(start); } while (start++ < last);
}
static void probe_data(uintptr_t base, uintptr_t index, uintptr_t disp, unsigned spec) {
  unsigned bytes = spec >> 5;
  uintptr_t addr = base + index * (spec & 15) + disp;
  if (spec & 16) addr = (uint32_t)addr;
  probe_ref(addr, bytes);
}
static void probe_skip(void) { ++probe_unsupported; }

static void probe_unsupported_op(void *ctx, instrlist_t *bb, instr_t *where) {
  dr_insert_clean_call(ctx, bb, where, (void *)probe_skip, false, 0);
}
static opnd_t probe_reg_arg(reg_id_t reg) {
  return reg == DR_REG_NULL ? OPND_CREATE_INTPTR(0) :
         opnd_create_reg(reg_to_pointer_sized(reg));
}
static void probe_operand(void *ctx, instrlist_t *bb, instr_t *where, opnd_t mem) {
  if (!opnd_is_memory_reference(mem))
    return;
  unsigned bytes = opnd_size_in_bytes(opnd_get_size(mem));
  if (!bytes || bytes > 64 || opnd_is_vsib(mem) ||
      opnd_get_segment(mem) == DR_SEG_FS || opnd_get_segment(mem) == DR_SEG_GS) {
    probe_unsupported_op(ctx, bb, where);
    return;
  }
  reg_id_t base = DR_REG_NULL, index = DR_REG_NULL;
  uintptr_t disp;
  unsigned spec = bytes << 5;
  if (opnd_is_base_disp(mem)) {
    base = opnd_get_base(mem);
    index = opnd_get_index(mem);
    if ((base != DR_REG_NULL && !reg_is_gpr(base)) ||
        (index != DR_REG_NULL && !reg_is_gpr(index))) {
      probe_unsupported_op(ctx, bb, where);
      return;
    }
    disp = (intptr_t)opnd_get_disp(mem);
    spec |= opnd_get_scale(mem) & 15;
    if ((base != DR_REG_NULL && reg_get_size(base) == OPSZ_4) ||
        (index != DR_REG_NULL && reg_get_size(index) == OPSZ_4) ||
        opnd_is_disp_short_addr(mem)) spec |= 16;
  } else if (opnd_is_rel_addr(mem) || opnd_is_abs_addr(mem)) {
    disp = (uintptr_t)opnd_get_addr(mem);
  } else {
    probe_unsupported_op(ctx, bb, where);
    return;
  }
  dr_insert_clean_call(ctx, bb, where, (void *)probe_data, false, 4,
                       probe_reg_arg(base), probe_reg_arg(index),
                       OPND_CREATE_INTPTR(disp), OPND_CREATE_INT32(spec));
}
/* Rep MOVS/STOS process both operands for every requested element in order.
 * Counts are attempted, pre-execution, and unsuitable after a fault or signal. */
static void probe_string(int op, unsigned bytes, bool addr32) {
  dr_mcontext_t mc;
  mc.size = sizeof(mc);
  mc.flags = DR_MC_INTEGER | DR_MC_CONTROL;
  if (!dr_get_mcontext(dr_get_current_drcontext(), &mc))
    fail("cache probe needs application context");
  uintptr_t src = mc.xsi, dst = mc.xdi, n = addr32 ? (uint32_t)mc.xcx : mc.xcx;
  uintptr_t step = (mc.xflags & (1u<<10)) ? (uintptr_t)-(intptr_t)bytes : bytes;
  ++probe_rep;
  for (uintptr_t j = 0; j < n; ++j) {
    if (op == OP_rep_movs) probe_ref(addr32 ? (uint32_t)src : src, bytes);
    probe_ref(addr32 ? (uint32_t)dst : dst, bytes);
    src += step;
    dst += step;
  }
}
static void probe_instrument(void *ctx, instrlist_t *bb, instr_t *where) {
  if (!instr_is_app(where))
    return;
  int op = instr_get_opcode(where);
  if (instr_is_rep_string_op(where)) {
    unsigned width = 0;
    bool addr32 = false, unsupported_segment = false;
    if (op == OP_rep_movs || op == OP_rep_stos) {
      for (int i = 0; i < instr_num_dsts(where); ++i) {
        opnd_t d = instr_get_dst(where, i);
        if (opnd_is_memory_reference(d))
          width = opnd_size_in_bytes(opnd_get_size(d));
      }
      for (int i = 0; i < instr_num_srcs(where); ++i) {
        opnd_t s = instr_get_src(where,i);
        if (opnd_is_reg(s) && opnd_get_reg(s) == DR_REG_ECX) addr32 = true;
        if (opnd_is_memory_reference(s) &&
            (opnd_get_segment(s) == DR_SEG_FS || opnd_get_segment(s) == DR_SEG_GS))
          unsupported_segment = true;
      }
    }
    if (!unsupported_segment && (width == 1 || width == 2 || width == 4 || width == 8))
      dr_insert_clean_call_ex(ctx, bb, where, (void *)probe_string,
                              DR_CLEANCALL_READS_APP_CONTEXT, 3,
                              OPND_CREATE_INT32(op), OPND_CREATE_INT32(width),
                              OPND_CREATE_INT32(addr32));
    else probe_unsupported_op(ctx, bb, where);
    return;
  }
  if (instr_is_prefetch(where) || op == OP_lea || op == OP_nop_modrm)
    return;
  if (instr_reads_memory(where)) {
    for (int i = 0; i < instr_num_srcs(where); ++i)
      probe_operand(ctx, bb, where, instr_get_src(where,i));
  }
  if (instr_writes_memory(where)) {
    for (int i = 0; i < instr_num_dsts(where); ++i)
      probe_operand(ctx, bb, where, instr_get_dst(where,i));
  }
}
static void probe_report(void) {
  dr_fprintf(STDERR, "PROBE_L1D refs=%llu lines=%llu misses=%llu skipped=%llu rep=%llu threads=%d\n",
             (unsigned long long)probe_refs, (unsigned long long)probe_lines,
             (unsigned long long)probe_misses, (unsigned long long)probe_unsupported,
             (unsigned long long)probe_rep, probe_threads);
}

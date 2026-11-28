/* -protect_meter: software fault isolation of the meter inside the measured
 * process. Included once by durante.c, after its accounting helpers; it uses
 * that file's thread state, raw TLS slots, streams and locks.
 *
 * The prepared runtime (-durante_window, see toolkit/patches) reserves one
 * 4GB-aligned window W PROT_NONE before the application is mapped and keeps
 * every runtime and client allocation inside it: the core image and its data,
 * the code cache, all heap (thread state, raw TLS, stacks), the client and its
 * private libraries, and the live work mapping. Nothing outside W may be
 * writable runtime memory, and nothing inside W belongs to the application.
 * The client audits that layout before the first application instruction.
 *
 * Every application store is then checked before it executes: its effective
 * address must not have the window's upper 32 bits. The inline check uses only
 * LEA, MOV, BSWAP and JRCXZ and restores its one scratch register, so it never
 * changes application registers or flags. The first and last 16MB of W stay
 * PROT_NONE, so a store or a sequential (REP, XSAVE) write that starts outside W
 * faults on a guard page before it can reach runtime memory. Within one block,
 * a store [R + d] needs no check while R is unchanged since an earlier checked
 * store [R + d0] with |d - d0| <= 1MB: both lie within the guard of each other.
 * Segment-relative stores, register bit offsets and scatters take a checked
 * callback; instructions that change FS/GS or cannot be decoded are refused.
 *
 * System calls are allow-listed. Output buffers, kernel-written user pointers,
 * mapping changes and signal stacks must not touch W; arrays the kernel reads
 * later are copied into the thread's state first. Descriptors of the client's
 * records and the runtime are refused, and every opened file is checked by
 * inode, path and file system after open.
 *
 * A detected attempt is an integrity violation: the live page is invalidated,
 * an integrity_violation record names the vector, and the process exits with
 * INTEGRITY_EXIT_CODE before the attempt takes effect. */

#include <sys/resource.h>
#include <sys/statfs.h>
#include <sys/sysmacros.h>
#include <sys/uio.h>

#define PROTECT_WINDOW_BYTES (UINT64_C(1) << 32)
#define PROTECT_GUARD_BYTES (UINT64_C(16) << 20)
#define PROTECT_ELIDE_RANGE (INT64_C(1) << 20)
#define PROTECT_SIGNAL_MARGIN (UINT64_C(256) << 10)
#define PROTECT_USER_TOP UINT64_C(0x800000000000)
#define PROC_SUPER_MAGIC_VALUE 0x9fa0
#define INTEGRITY_EXIT_CODE 125
#ifndef O_PATH
#define O_PATH 010000000
#endif

static ptr_uint_t protect_window;
static uint32_t protect_window_key; /* bswap32 of the window's upper half */
static int protect_min_dr_fd = INT32_MAX;
static uint64_t protect_outdir_dev, protect_outdir_ino;
static size_t protect_outdir_length;
static uint64_t protect_scatter_sites;

static bool protect_overlaps(ptr_uint_t start, ptr_uint_t length) {
  if (!length)
    return false;
  ptr_uint_t last = start + length - 1;
  if (last < start)
    last = UINTPTR_MAX;
  return start < protect_window + PROTECT_WINDOW_BYTES && last >= protect_window;
}

static bool protect_contains(const void *start, size_t length) {
  ptr_uint_t first = (ptr_uint_t)start;
  return first >= protect_window && length <= PROTECT_WINDOW_BYTES &&
         first - protect_window <= PROTECT_WINDOW_BYTES - length;
}

static uint64_t *protect_violation_slot(thread_data_t *d) {
  return d && d->raw_tls_base ? (uint64_t *)(d->raw_tls_base + raw_tls_offset) + RAW_TLS_VIOLATION
                              : NULL;
}

/* Never returns. vector is one of the fixed names the collectors recognize. */
static void integrity_violation(void *ctx, const char *vector) {
  invalidate_live_work();
  __atomic_store_n(&static_text_revoked, true, __ATOMIC_RELEASE);
  thread_data_t *d = ctx ? drmgr_get_tls_field(ctx, tls_index) : NULL;
  char fields[96];
  dr_snprintf(fields, sizeof(fields), ",\"vector\":\"%s\"", vector);
  dr_mutex_lock(state_lock);
  route_event(d, "integrity_violation", fields);
  dr_mutex_unlock(state_lock);
  dr_fprintf(STDERR, "durante: integrity violation: %s\n", vector);
  dr_abort_with_code(INTEGRITY_EXIT_CODE);
}

/* ------------------------------------------------------------------------ */
/* Layout */

static void protect_check_thread(thread_data_t *d) {
  if (!protect_meter)
    return;
  if (!protect_contains(d, sizeof(*d)) || !d->raw_tls_base ||
      !protect_contains(d->raw_tls_base + raw_tls_offset, RAW_TLS_PROTECT_SLOTS * sizeof(void *)) ||
      (d->boundary_stack && !protect_contains(d->boundary_stack, BOUNDARY_STACK_BYTES)))
    fail("meter protection: thread state outside the protected window");
  *protect_violation_slot(d) = 0;
}

/* Before the first application instruction: every writable runtime or client
 * mapping lies inside the window, and the window has no unreserved hole and no
 * foreign mapping. Later changes inside the window can only come from the
 * runtime: application mappings there are refused. */
static void protect_audit_layout(void) {
  if (!protect_contains(&protect_window, sizeof(protect_window)) ||
      !protect_contains((void *)dr_get_current_drcontext, 1) ||
      (live_work && !protect_contains(live_work, sizeof(*live_work))))
    fail("meter protection: runtime or client outside the protected window");
  byte *pc = NULL;
  for (uint regions = 0; (ptr_uint_t)pc < PROTECT_USER_TOP; ++regions) {
    dr_mem_info_t info;
    if (regions > 1000000 || !dr_query_memory_ex(pc, &info))
      fail("meter protection: cannot audit the address space");
    ptr_uint_t start = (ptr_uint_t)info.base_pc, end = start + info.size;
    if (end < start || end > PROTECT_USER_TOP)
      end = PROTECT_USER_TOP;
    if (end <= (ptr_uint_t)pc || start > (ptr_uint_t)pc)
      fail("meter protection: inconsistent address-space audit");
    if (protect_overlaps(start, info.size)) {
      /* The runtime reserved the whole window before the application image was
       * mapped and refuses application images inside it. */
      if (info.type == DR_MEMTYPE_FREE || start < protect_window ||
          end > protect_window + PROTECT_WINDOW_BYTES)
        fail("meter protection: the protected window is not fully reserved");
    } else if (info.type != DR_MEMTYPE_FREE && (info.prot & DR_MEMPROT_WRITE) &&
               (dr_memory_is_dr_internal(info.base_pc) || dr_memory_is_in_client(info.base_pc))) {
      fail("meter protection: writable runtime memory outside the protected window");
    }
    pc = (byte *)end;
  }
  /* The runtime keeps its descriptors at the top of the descriptor table. */
  struct rlimit limit;
  uint64 steal = 0;
  if (getrlimit(RLIMIT_NOFILE, &limit) == 0 && dr_get_integer_option("steal_fds", &steal) &&
      steal && limit.rlim_cur > steal && limit.rlim_cur <= INT32_MAX)
    protect_min_dr_fd = (int)(limit.rlim_cur - steal);
  struct stat st;
  int dir = open(outdir, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
  if (dir < 0 || fstat(dir, &st) != 0)
    fail("meter protection: cannot identify the report directory");
  close(dir);
  protect_outdir_dev = st.st_dev;
  protect_outdir_ino = st.st_ino;
  protect_outdir_length = strlen(outdir);
  while (protect_outdir_length > 1 && outdir[protect_outdir_length - 1] == '/')
    --protect_outdir_length;
}

static void protect_init(void) {
  uint64 window = 0;
  if (!dr_get_integer_option("durante_window", &window) || !window ||
      (window & (PROTECT_WINDOW_BYTES - 1)) || window > PROTECT_USER_TOP - PROTECT_WINDOW_BYTES)
    fail("-protect_meter requires the prepared runtime with -durante_window");
  protect_window = (ptr_uint_t)window;
  protect_window_key = __builtin_bswap32((uint32_t)(window >> 32));
}

/* ------------------------------------------------------------------------ */
/* Store checks */

typedef struct {
  bool valid[16];
  int64_t disp0[16], delta[16];
} protect_block_t;

static int protect_gpr(reg_id_t reg) {
  return reg >= DR_REG_RAX && reg <= DR_REG_R15 ? (int)(reg - DR_REG_RAX) : -1;
}

static void protect_forget_all(protect_block_t *pb) {
  memset(pb, 0, sizeof(*pb));
}

#define PROTECT_INSERT(i) instrlist_meta_preinsert(bb, where, (i))

/* A check of the address that `addr` (an LEA operand) computes. When seg is FS
 * or GS, the application's segment base is added first; addr then names
 * neither RCX as base nor as index. */
static void protect_insert_check(void *ctx, instrlist_t *bb, instr_t *where, opnd_t addr,
                                 reg_id_t seg) {
  instr_t *violation = INSTR_CREATE_label(ctx), *done = INSTR_CREATE_label(ctx);
  opnd_t rcx = opnd_create_reg(DR_REG_RCX);
  PROTECT_INSERT(INSTR_CREATE_mov_st(ctx, raw_slot(RAW_TLS_SPILL_RCX), rcx));
  if (seg != DR_REG_NULL) {
    if (!dr_insert_get_seg_base(ctx, bb, where, seg, DR_REG_RCX))
      fail("meter protection: cannot read an application segment base");
    reg_id_t base = opnd_get_base(addr), index = opnd_get_index(addr);
    if (index != DR_REG_NULL)
      PROTECT_INSERT(INSTR_CREATE_lea(
          ctx, rcx, opnd_create_base_disp(DR_REG_RCX, index, opnd_get_scale(addr), 0, OPSZ_lea)));
    PROTECT_INSERT(INSTR_CREATE_lea(
        ctx, rcx, opnd_create_base_disp(DR_REG_RCX, base, base == DR_REG_NULL ? 0 : 1,
                                        opnd_get_disp(addr), OPSZ_lea)));
  } else {
    PROTECT_INSERT(INSTR_CREATE_lea(ctx, rcx, addr));
  }
  PROTECT_INSERT(INSTR_CREATE_bswap(ctx, rcx));
  PROTECT_INSERT(INSTR_CREATE_mov_ld(ctx, opnd_create_reg(DR_REG_ECX), opnd_create_reg(DR_REG_ECX)));
  PROTECT_INSERT(INSTR_CREATE_lea(
      ctx, rcx, opnd_create_base_disp(DR_REG_RCX, DR_REG_NULL, 0, -(int)protect_window_key, OPSZ_lea)));
  PROTECT_INSERT(INSTR_CREATE_jecxz(ctx, opnd_create_instr(violation)));
  PROTECT_INSERT(INSTR_CREATE_mov_ld(ctx, rcx, raw_slot(RAW_TLS_SPILL_RCX)));
  PROTECT_INSERT(INSTR_CREATE_jmp_short(ctx, opnd_create_instr(done)));
  PROTECT_INSERT(violation);
  opnd_t flag = raw_slot(RAW_TLS_VIOLATION);
  opnd_set_size(&flag, OPSZ_1);
  PROTECT_INSERT(INSTR_CREATE_mov_st(ctx, flag, OPND_CREATE_INT8(1)));
  /* The trap reaches signal_event(), which finds the flag and fails closed. */
  instr_t *trap = INSTR_CREATE_ud2(ctx);
  instr_set_translation(trap, instr_get_app_pc(where));
  PROTECT_INSERT(trap);
  PROTECT_INSERT(done);
}

enum {
  PROTECT_VECTOR_STATIC, PROTECT_VECTOR_UNDECODED, PROTECT_VECTOR_SEGMENT_REGISTER,
  PROTECT_VECTOR_EXOTIC
};

static void protect_static_violation(int vector) {
  static const char *const names[] = {"memory_store", "unsupported_instruction",
                                      "segment_register", "unsupported_store"};
  integrity_violation(dr_get_current_drcontext(),
                      vector >= 0 && vector < (int)(sizeof(names) / sizeof(names[0]))
                          ? names[vector]
                          : "unsupported_instruction");
}

static void protect_insert_violation(void *ctx, instrlist_t *bb, instr_t *where, int vector) {
  dr_insert_clean_call(ctx, bb, where, (void *)protect_static_violation, false, 1,
                       OPND_CREATE_INT32(vector));
}

static bool protect_range_unsafe(app_pc start, size_t length) {
  return protect_overlaps((ptr_uint_t)start, length ? length : 1);
}

static app_pc protect_operand_address(reg_id_t seg, reg_id_t base, reg_id_t index, int scale,
                                      int disp, dr_mcontext_t *mc) {
  opnd_t operand = seg == DR_REG_NULL
                       ? opnd_create_base_disp(base, index, scale, disp, OPSZ_1)
                       : opnd_create_far_base_disp(seg, base, index, scale, disp, OPSZ_1);
  return opnd_compute_address(operand, mc);
}

/* A segment-relative store whose operand also uses RCX. */
static void protect_check_far(uint regs, uint seg_scale, int disp, uint size) {
  void *ctx = dr_get_current_drcontext();
  dr_mcontext_t mc = {.size = sizeof(mc), .flags = DR_MC_INTEGER | DR_MC_CONTROL};
  if (!dr_get_mcontext(ctx, &mc))
    integrity_violation(ctx, "segment_store");
  app_pc addr = protect_operand_address((reg_id_t)(seg_scale & 0xffff), (reg_id_t)(regs & 0xffff),
                                        (reg_id_t)(regs >> 16), (int)(seg_scale >> 16), disp, &mc);
  if (protect_range_unsafe(addr, size))
    integrity_violation(ctx, "segment_store");
}

/* BTS/BTR/BTC with a register bit offset address OPERAND + floor(offset /
 * bits) * bytes, which can be anywhere. */
static void protect_check_bitop(uint regs, uint seg_scale, int disp, uint bit_reg_size) {
  void *ctx = dr_get_current_drcontext();
  dr_mcontext_t mc = {.size = sizeof(mc), .flags = DR_MC_INTEGER | DR_MC_CONTROL};
  if (!dr_get_mcontext(ctx, &mc))
    integrity_violation(ctx, "bit_store");
  app_pc addr = protect_operand_address((reg_id_t)(seg_scale & 0xffff), (reg_id_t)(regs & 0xffff),
                                        (reg_id_t)(regs >> 16), (int)(seg_scale >> 16), disp, &mc);
  reg_id_t bit_reg = (reg_id_t)(bit_reg_size & 0xffff);
  uint bytes = bit_reg_size >> 16;
  reg_t raw = reg_get_value(bit_reg, &mc);
  int64_t offset = bytes == 2 ? (int16_t)raw : bytes == 4 ? (int32_t)raw : (int64_t)raw;
  int64_t bits = (int64_t)bytes * 8, unit = offset / bits;
  if (offset % bits < 0)
    --unit;
  if (bytes != 2 && bytes != 4 && bytes != 8)
    integrity_violation(ctx, "bit_store");
  if (protect_range_unsafe(addr + unit * (int64_t)bytes, bytes))
    integrity_violation(ctx, "bit_store");
}

typedef struct {
  app_pc pc;
  uint length;
  byte bytes[16];
} protect_scatter_t;

/* Scatters address one element per vector lane. Every lane DR computes is
 * checked, including lanes the mask may skip. */
static void protect_check_scatter(protect_scatter_t *site) {
  void *ctx = dr_get_current_drcontext();
  dr_mcontext_t mc = {.size = sizeof(mc), .flags = DR_MC_ALL};
  instr_t instr;
  instr_init(ctx, &instr);
  if (!dr_get_mcontext(ctx, &mc) ||
      decode_from_copy(ctx, site->bytes, site->pc, &instr) != site->bytes + site->length)
    integrity_violation(ctx, "scatter_store");
  app_pc addr;
  bool write;
  uint position;
  for (uint i = 0; instr_compute_address_ex_pos(&instr, &mc, i, &addr, &write, &position); ++i) {
    if (!write)
      continue;
    opnd_t operand = instr_get_dst(&instr, position);
    uint size = opnd_size_in_bytes(opnd_get_size(operand));
    if (protect_range_unsafe(addr, size ? size : 64))
      integrity_violation(ctx, "scatter_store");
  }
  instr_free(ctx, &instr);
}

static bool protect_segment(reg_id_t seg) {
  return seg == DR_SEG_FS || seg == DR_SEG_GS;
}

static bool protect_writes_segment(instr_t *where) {
  int op = instr_get_opcode(where);
  if (op == OP_wrfsbase || op == OP_wrgsbase || op == OP_lfs || op == OP_lgs)
    return true;
  for (int i = 0; i < instr_num_dsts(where); ++i) {
    opnd_t dst = instr_get_dst(where, i);
    if (opnd_is_reg(dst) && protect_segment(opnd_get_reg(dst)))
      return true;
  }
  return false;
}

static bool protect_exotic_store(int op) {
  return op == OP_enqcmd || op == OP_enqcmds || op == OP_bndstx;
}

/* Track how each GPR moved since the store that last validated it. */
static void protect_update_registers(instr_t *where, protect_block_t *pb) {
  int op = instr_get_opcode(where);
  int64_t adjust = 0;
  bool known = false;
  if (op == OP_push || op == OP_push_imm || op == OP_pushf) {
    /* The stack slot written is the instruction's memory destination. */
    for (int i = 0; i < instr_num_dsts(where) && !known; ++i) {
      opnd_t dst = instr_get_dst(where, i);
      if (opnd_is_memory_reference(dst)) {
        adjust = -(int64_t)opnd_size_in_bytes(opnd_get_size(dst));
        known = true;
      }
    }
  } else if (op == OP_pop || op == OP_popf) {
    /* The stack slot read is the RSP-based memory source. A POP into RSP
     * itself (its first destination) loads an arbitrary value. */
    opnd_t first = instr_num_dsts(where) > 0 ? instr_get_dst(where, 0) : opnd_create_null();
    bool into_rsp = op == OP_pop && opnd_is_reg(first) && reg_overlap(opnd_get_reg(first), DR_REG_RSP);
    for (int i = 0; i < instr_num_srcs(where) && !known && !into_rsp; ++i) {
      opnd_t src = instr_get_src(where, i);
      if (opnd_is_memory_reference(src) && opnd_is_base_disp(src) &&
          opnd_get_base(src) == DR_REG_RSP) {
        adjust = (int64_t)opnd_size_in_bytes(opnd_get_size(src));
        known = true;
      }
    }
  } else if ((op == OP_add || op == OP_sub) && instr_num_dsts(where) >= 1 &&
             opnd_is_reg(instr_get_dst(where, 0)) &&
             opnd_get_reg(instr_get_dst(where, 0)) == DR_REG_RSP &&
             opnd_is_immed_int(instr_get_src(where, 0))) {
    adjust = opnd_get_immed_int(instr_get_src(where, 0));
    if (op == OP_sub)
      adjust = -adjust;
    known = true;
  } else if (op == OP_lea && opnd_is_reg(instr_get_dst(where, 0)) &&
             opnd_get_reg(instr_get_dst(where, 0)) == DR_REG_RSP) {
    opnd_t src = instr_get_src(where, 0);
    if (opnd_is_base_disp(src) && opnd_get_base(src) == DR_REG_RSP &&
        opnd_get_index(src) == DR_REG_NULL && opnd_get_segment(src) == DR_REG_NULL) {
      adjust = opnd_get_disp(src);
      known = true;
    }
  }
  for (reg_id_t reg = DR_REG_RAX; reg <= DR_REG_R15; ++reg) {
    int g = protect_gpr(reg);
    if (!instr_writes_to_reg(where, reg, DR_QUERY_INCLUDE_ALL))
      continue;
    if (reg == DR_REG_RSP && known && pb->valid[g]) {
      pb->delta[g] += adjust;
      if (pb->delta[g] > PROTECT_ELIDE_RANGE || pb->delta[g] < -PROTECT_ELIDE_RANGE)
        pb->valid[g] = false;
    } else {
      pb->valid[g] = false;
    }
  }
}

/* Before each application instruction of a block, in order. */
static void protect_instrument(void *ctx, instrlist_t *bb, instr_t *where, bool translating,
                               protect_block_t *pb) {
  int op = instr_get_opcode(where);
  if (op == OP_INVALID || op == OP_UNDECODED) {
    protect_insert_violation(ctx, bb, where, PROTECT_VECTOR_UNDECODED);
    protect_forget_all(pb);
    return;
  }
  if (protect_writes_segment(where)) {
    protect_insert_violation(ctx, bb, where, PROTECT_VECTOR_SEGMENT_REGISTER);
    protect_forget_all(pb);
    return;
  }
  if (instr_writes_memory(where) && protect_exotic_store(op)) {
    protect_insert_violation(ctx, bb, where, PROTECT_VECTOR_EXOTIC);
    protect_forget_all(pb);
    return;
  }
  for (int i = 0; instr_writes_memory(where) && i < instr_num_dsts(where); ++i) {
    opnd_t dst = instr_get_dst(where, i);
    if (!opnd_is_memory_reference(dst))
      continue;
    reg_id_t seg = opnd_get_segment(dst);
    if (!protect_segment(seg))
      seg = DR_REG_NULL;
    uint size = opnd_size_in_bytes(opnd_get_size(dst));
    if (opnd_is_rel_addr(dst) || opnd_is_abs_addr(dst)) {
      if (seg != DR_REG_NULL) {
        /* An absolute segment offset, as for TLS: fs:[disp]. */
        protect_insert_check(ctx, bb, where,
                             opnd_create_base_disp(DR_REG_NULL, DR_REG_NULL, 0,
                                                   (int)(ptr_int_t)opnd_get_addr(dst), OPSZ_lea),
                             seg);
      } else if (protect_range_unsafe(opnd_get_addr(dst), size ? size : PROTECT_GUARD_BYTES)) {
        protect_insert_violation(ctx, bb, where, PROTECT_VECTOR_STATIC);
      }
      continue;
    }
    if (!opnd_is_base_disp(dst)) {
      protect_insert_violation(ctx, bb, where, PROTECT_VECTOR_EXOTIC);
      continue;
    }
    reg_id_t base = opnd_get_base(dst), index = opnd_get_index(dst);
    int scale = opnd_get_scale(dst), disp = opnd_get_disp(dst);
    if (index != DR_REG_NULL && !reg_is_gpr(index)) {
      /* VSIB: one address per lane. */
      if (++protect_scatter_sites > (UINT64_C(1) << 20))
        fail("meter protection: too many scatter sites");
      protect_scatter_t *site = NULL;
      if (!translating) {
        site = dr_global_alloc(sizeof(*site));
        site->pc = instr_get_app_pc(where);
        site->length = (uint)instr_length(ctx, where);
        if (site->length > sizeof(site->bytes) ||
            !dr_safe_read(site->pc, site->length, site->bytes, NULL))
          fail("meter protection: cannot copy a scatter instruction");
      }
      dr_insert_clean_call_ex(ctx, bb, where, (void *)protect_check_scatter,
                              DR_CLEANCALL_READS_APP_CONTEXT, 1,
                              OPND_CREATE_INTPTR(translating ? (void *)protect_window : site));
      continue;
    }
    uint regs = (uint)base | (uint)index << 16, seg_scale = (uint)seg | (uint)scale << 16;
    if ((op == OP_bts || op == OP_btr || op == OP_btc) && instr_num_srcs(where) > 0 &&
        opnd_is_reg(instr_get_src(where, 0))) {
      dr_insert_clean_call_ex(ctx, bb, where, (void *)protect_check_bitop,
                              DR_CLEANCALL_READS_APP_CONTEXT, 4, OPND_CREATE_INT32(regs),
                              OPND_CREATE_INT32(seg_scale), OPND_CREATE_INT32(disp),
                              OPND_CREATE_INT32((uint)opnd_get_reg(instr_get_src(where, 0)) |
                                                size << 16));
      continue;
    }
    if (op == OP_pop && base == DR_REG_RSP)
      disp += (int)size; /* The address follows the pop's own RSP increment. */
    if (seg != DR_REG_NULL) {
      if (base == DR_REG_RCX || index == DR_REG_RCX || reg_overlap(base, DR_REG_RCX) ||
          (index != DR_REG_NULL && reg_overlap(index, DR_REG_RCX)))
        dr_insert_clean_call_ex(ctx, bb, where, (void *)protect_check_far,
                                DR_CLEANCALL_READS_APP_CONTEXT, 4, OPND_CREATE_INT32(regs),
                                OPND_CREATE_INT32(seg_scale), OPND_CREATE_INT32(disp),
                                OPND_CREATE_INT32(size ? size : 64));
      else
        protect_insert_check(ctx, bb, where,
                             opnd_create_base_disp(base, index, scale, disp, OPSZ_lea), seg);
      continue;
    }
    int g = index == DR_REG_NULL && reg_is_64bit(base) ? protect_gpr(base) : -1;
    if (g >= 0 && pb->valid[g]) {
      int64_t moved = pb->delta[g] + disp - pb->disp0[g];
      if (moved <= PROTECT_ELIDE_RANGE && moved >= -PROTECT_ELIDE_RANGE)
        continue;
    }
    protect_insert_check(ctx, bb, where, opnd_create_base_disp(base, index, scale, disp, OPSZ_lea),
                         DR_REG_NULL);
    if (g >= 0) {
      pb->valid[g] = true;
      pb->disp0[g] = disp;
      pb->delta[g] = 0;
    }
  }
  protect_update_registers(where, pb);
}

#undef PROTECT_INSERT

/* ------------------------------------------------------------------------ */
/* Signals */

static void protect_signal(void *ctx, thread_data_t *d, dr_siginfo_t *info) {
  uint64_t *slot = protect_violation_slot(d);
  if (slot && *slot)
    integrity_violation(ctx, "memory_store");
  /* The runtime writes a handler's frame below the interrupted stack pointer
   * (or on the alternate stack, which sigaltstack keeps outside the window). */
  if (info->mcontext &&
      protect_overlaps(info->mcontext->xsp - PROTECT_SIGNAL_MARGIN, PROTECT_SIGNAL_MARGIN + 16))
    integrity_violation(ctx, "signal_stack");
}

/* ------------------------------------------------------------------------ */
/* System calls */

enum { PROTECT_MAX_IOV = 64 };

static bool protect_fd(thread_data_t *d, reg_t value) {
  int fd = (int)value;
  return (int64_t)value == fd && fd >= 0 &&
         (fd == report_file || (d && d->child_file >= 0 && fd == d->child_file) ||
          fd >= protect_min_dr_fd);
}

static reg_t protect_param(void *ctx, int i) {
  return dr_syscall_get_param(ctx, i);
}

#define PROTECT_OUT(ptr, length)                                                         \
  do {                                                                                   \
    if (protect_overlaps((ptr_uint_t)(ptr), (ptr_uint_t)(length)))                        \
      integrity_violation(ctx, "syscall_output");                                        \
  } while (0)
#define PROTECT_OUT_PARAM(i, length)                                                      \
  do {                                                                                   \
    reg_t p_ = protect_param(ctx, i);                                                    \
    if (p_)                                                                              \
      PROTECT_OUT(p_, length);                                                           \
  } while (0)
#define PROTECT_FD(i)                                                                     \
  do {                                                                                   \
    if (protect_fd(d, protect_param(ctx, i)))                                            \
      integrity_violation(ctx, "protected_fd");                                          \
  } while (0)
#define PROTECT_MAP(start, length)                                                        \
  do {                                                                                   \
    if (protect_overlaps((ptr_uint_t)(start), (ptr_uint_t)(length) ? (length) : 1))      \
      integrity_violation(ctx, "memory_map");                                            \
  } while (0)

/* Replace parameter i, for this syscall only, with a copy in the thread's state. */
static void protect_rewrite(thread_data_t *d, int i, void *copy) {
  d->protect_param = i;
  d->protect_param_value = (reg_t)copy;
}

static bool protect_deny(void *ctx, int error) {
  dr_syscall_set_result(ctx, (reg_t)-error);
  return false;
}

/* A best-effort report for path operations that are refused anyway. */
static void protect_check_path(void *ctx, reg_t pointer) {
  char path[sizeof(outdir)];
  size_t got = 0;
  if (!pointer || !dr_safe_read((void *)pointer, sizeof(path) - 1, path, &got)) {
    if (!pointer || !got)
      return;
  }
  path[got < sizeof(path) ? got : sizeof(path) - 1] = '\0';
  if (protect_outdir_length && strncmp(path, outdir, protect_outdir_length) == 0 &&
      (path[protect_outdir_length] == '/' || path[protect_outdir_length] == '\0'))
    integrity_violation(ctx, "protected_file");
}

static bool protect_readv(void *ctx, thread_data_t *d, int iov_param, int count_param) {
  reg_t count = protect_param(ctx, count_param);
  if (count > PROTECT_MAX_IOV)
    return protect_deny(ctx, EINVAL);
  size_t bytes = (size_t)count * sizeof(struct iovec);
  if (count && !dr_safe_read((void *)protect_param(ctx, iov_param), bytes, d->protect_iov, NULL))
    return protect_deny(ctx, EFAULT);
  for (reg_t i = 0; i < count; ++i)
    PROTECT_OUT(d->protect_iov[i].iov_base, d->protect_iov[i].iov_len);
  protect_rewrite(d, iov_param, d->protect_iov);
  return true;
}

static bool protect_clone_flags(void *ctx, uint64_t flags, reg_t ptid, reg_t ctid) {
  if (flags & (CLONE_VM_FLAG)) {
    if (!(flags & CLONE_THREAD_FLAG))
      return protect_deny(ctx, EPERM); /* No shared-VM processes (vfork, posix_spawn). */
  }
  if (flags & (UINT64_C(0x00100000) /* PARENT_SETTID */ | UINT64_C(0x00001000) /* PIDFD */))
    PROTECT_OUT(ptid, sizeof(int));
  if (flags & (UINT64_C(0x01000000) /* CHILD_SETTID */ | UINT64_C(0x00200000) /* CHILD_CLEARTID */))
    PROTECT_OUT(ctid, sizeof(int));
  return true;
}

static bool protect_prctl(void *ctx) {
  reg_t option = protect_param(ctx, 0);
  switch (option) {
    case 1: case 3: case 15: case 23: case 21: case 29: case 30: case 38: case 39: case 41:
    case 42: case 0x53564d41:
      return true;
    case 2: PROTECT_OUT_PARAM(1, sizeof(int)); return true;
    case 16: PROTECT_OUT_PARAM(1, 16); return true;
    case 40: PROTECT_OUT_PARAM(1, sizeof(void *)); return true;
    case 22: integrity_violation(ctx, "seccomp"); return false;
    default: return protect_deny(ctx, EINVAL);
  }
}

static bool protect_fcntl(void *ctx, thread_data_t *d) {
  PROTECT_FD(0);
  switch (protect_param(ctx, 1)) {
    case 0: case 1: case 2: case 3: case 4: case 6: case 7: case 37: case 38: case 1030:
    case 1031: case 1032: case 1033: case 1034:
      return true;
    case 5: case 36: PROTECT_OUT_PARAM(2, 32); return true;
    default: return protect_deny(ctx, EINVAL);
  }
}

static bool protect_ioctl(void *ctx, thread_data_t *d) {
  PROTECT_FD(0);
  switch (protect_param(ctx, 1)) {
    case 0x5401: PROTECT_OUT_PARAM(2, 64); return true;       /* TCGETS */
    case 0x5413: PROTECT_OUT_PARAM(2, 8); return true;        /* TIOCGWINSZ */
    case 0x541B: case 0x540F: PROTECT_OUT_PARAM(2, 4); return true; /* FIONREAD, TIOCGPGRP */
    case 0x5421: case 0x5450: case 0x5451: return true;       /* FIONBIO, FIONCLEX, FIOCLEX */
    default: return protect_deny(ctx, ENOTTY);
  }
}

static bool protect_futex(void *ctx) {
  reg_t op = protect_param(ctx, 1) & ~(reg_t)(128 | 256);
  PROTECT_OUT_PARAM(0, sizeof(int));
  if (op == 3 || op == 4 || op == 5 || op == 11 || op == 12)
    PROTECT_OUT_PARAM(4, sizeof(int));
  return op <= 13 ? true : protect_deny(ctx, ENOSYS);
}

static bool protect_select(void *ctx, bool pselect) {
  reg_t nfds = protect_param(ctx, 0);
  if (nfds > 1024 * 1024)
    return protect_deny(ctx, EINVAL);
  size_t bytes = (size_t)((nfds + 63) / 64) * 8;
  for (int i = 1; i <= 3; ++i)
    PROTECT_OUT_PARAM(i, bytes);
  (void)pselect; /* struct timeval and struct timespec are both 16 bytes. */
  PROTECT_OUT_PARAM(4, 16);
  return true;
}

/* Returns false when the syscall must not run: denied with an error number,
 * or emulated. A violation does not return. */
static bool protect_pre_syscall(void *ctx, int number, thread_data_t *d) {
  d->protect_param = -1;
  d->protect_open = false;
  d->protect_map = false;
  if (x32_syscall(number))
    integrity_violation(ctx, "syscall_abi");
  switch (number) {
    /* No pointer arguments the kernel writes. */
    case SYS_sched_yield: case SYS_getpid: case SYS_gettid: case SYS_getuid: case SYS_getgid:
    case SYS_geteuid: case SYS_getegid: case SYS_getppid: case SYS_getpgrp: case SYS_getpgid:
    case SYS_getsid: case SYS_umask: case SYS_kill: case SYS_tkill: case SYS_tgkill:
    case SYS_exit: case SYS_exit_group: case SYS_rt_sigreturn: case SYS_rt_sigsuspend:
    case SYS_pause: case SYS_alarm: case SYS_getpriority: case SYS_setpriority:
    case SYS_sched_getscheduler: case SYS_sched_setscheduler: case SYS_sched_setaffinity:
    case SYS_sched_get_priority_max: case SYS_sched_get_priority_min: case SYS_setrlimit:
    case SYS_access: case SYS_faccessat: case SYS_brk: case SYS_close: case SYS_fork:
#ifdef SYS_faccessat2
    case SYS_faccessat2:
#endif
      return true;
    case SYS_lseek: case SYS_fsync: case SYS_fdatasync:
      PROTECT_FD(0);
      return true;
    case SYS_write: case SYS_pwrite64: case SYS_writev: case SYS_pwritev:
#ifdef SYS_pwritev2
    case SYS_pwritev2:
#endif
      PROTECT_FD(0);
      return true;
    case SYS_read: case SYS_pread64:
      PROTECT_OUT_PARAM(1, protect_param(ctx, 2));
      return true;
    case SYS_readv: case SYS_preadv:
#ifdef SYS_preadv2
    case SYS_preadv2:
#endif
      return protect_readv(ctx, d, 1, 2);
    case SYS_getdents: case SYS_getdents64: PROTECT_OUT_PARAM(1, protect_param(ctx, 2)); return true;
    case SYS_getrandom: case SYS_getcwd: PROTECT_OUT_PARAM(0, protect_param(ctx, 1)); return true;
    case SYS_readlink: PROTECT_OUT_PARAM(1, protect_param(ctx, 2)); return true;
    case SYS_readlinkat: PROTECT_OUT_PARAM(2, protect_param(ctx, 3)); return true;
    case SYS_fstat: PROTECT_OUT_PARAM(1, sizeof(struct stat)); return true;
    case SYS_stat: case SYS_lstat: PROTECT_OUT_PARAM(1, sizeof(struct stat)); return true;
    case SYS_newfstatat: PROTECT_OUT_PARAM(2, sizeof(struct stat)); return true;
#ifdef SYS_statx
    case SYS_statx: PROTECT_OUT_PARAM(4, 256); return true;
#endif
    case SYS_statfs: case SYS_fstatfs: PROTECT_OUT_PARAM(1, sizeof(struct statfs)); return true;
    case SYS_poll: PROTECT_OUT_PARAM(0, protect_param(ctx, 1) * 8); return true;
    case SYS_ppoll:
      PROTECT_OUT_PARAM(0, protect_param(ctx, 1) * 8);
      PROTECT_OUT_PARAM(2, 16);
      return true;
    case SYS_select: return protect_select(ctx, false);
    case SYS_pselect6: return protect_select(ctx, true);
    case SYS_pipe: case SYS_pipe2: PROTECT_OUT_PARAM(0, 2 * sizeof(int)); return true;
    case SYS_dup: case SYS_dup2: case SYS_dup3: PROTECT_FD(0); return true;
    case SYS_fcntl: return protect_fcntl(ctx, d);
    case SYS_ioctl: return protect_ioctl(ctx, d);
    case SYS_open: case SYS_creat: case SYS_openat:
      d->protect_open = true;
      d->protect_open_flags =
          (int)protect_param(ctx, number == SYS_openat ? 2 : 1) | (number == SYS_creat ? O_WRONLY : 0);
      return true;
    case SYS_mmap:
      if (!(protect_param(ctx, 3) & 0x20 /* MAP_ANONYMOUS */))
        PROTECT_FD(4);
      if (protect_param(ctx, 3) & (MAP_FIXED | 0x100000 /* MAP_FIXED_NOREPLACE */))
        PROTECT_MAP(protect_param(ctx, 0), protect_param(ctx, 1));
      d->protect_map = true;
      return true;
    case SYS_munmap: case SYS_mprotect: case SYS_madvise:
#ifdef SYS_pkey_mprotect
    case SYS_pkey_mprotect:
#endif
      PROTECT_MAP(protect_param(ctx, 0), protect_param(ctx, 1));
      return true;
    case SYS_mremap:
      PROTECT_MAP(protect_param(ctx, 0), protect_param(ctx, 1));
      if (protect_param(ctx, 3) & 2 /* MREMAP_FIXED */)
        PROTECT_MAP(protect_param(ctx, 4), protect_param(ctx, 2));
      d->protect_map = true;
      return true;
    case SYS_rt_sigaction: PROTECT_OUT_PARAM(2, 32); return true;
    case SYS_rt_sigprocmask: PROTECT_OUT_PARAM(2, protect_param(ctx, 3)); return true;
    case SYS_rt_sigpending: PROTECT_OUT_PARAM(0, protect_param(ctx, 1)); return true;
    case SYS_rt_sigtimedwait: PROTECT_OUT_PARAM(1, 128); return true;
    case SYS_sigaltstack: {
      reg_t ss = protect_param(ctx, 0);
      PROTECT_OUT_PARAM(1, sizeof(stack_t));
      if (!ss)
        return true;
      if (!dr_safe_read((void *)ss, sizeof(stack_t), &d->protect_stack, NULL))
        return protect_deny(ctx, EFAULT);
      if (!(d->protect_stack.ss_flags & SS_DISABLE) &&
          protect_overlaps((ptr_uint_t)d->protect_stack.ss_sp - PROTECT_SIGNAL_MARGIN,
                           d->protect_stack.ss_size + PROTECT_SIGNAL_MARGIN + 16))
        integrity_violation(ctx, "signal_stack");
      protect_rewrite(d, 0, &d->protect_stack);
      return true;
    }
    case SYS_nanosleep: PROTECT_OUT_PARAM(1, 16); return true;
    case SYS_clock_nanosleep: PROTECT_OUT_PARAM(3, 16); return true;
    case SYS_clock_gettime: case SYS_clock_getres: PROTECT_OUT_PARAM(1, 16); return true;
    case SYS_gettimeofday: PROTECT_OUT_PARAM(0, 16); PROTECT_OUT_PARAM(1, 8); return true;
    case SYS_time: PROTECT_OUT_PARAM(0, 8); return true;
    case SYS_times: PROTECT_OUT_PARAM(0, 32); return true;
    case SYS_getrusage: PROTECT_OUT_PARAM(1, 144); return true;
    case SYS_getrlimit: PROTECT_OUT_PARAM(1, 16); return true;
    case SYS_prlimit64: PROTECT_OUT_PARAM(3, 16); return true;
    case SYS_sysinfo: PROTECT_OUT_PARAM(0, 112); return true;
    case SYS_uname: PROTECT_OUT_PARAM(0, 390); return true;
    case SYS_getgroups: PROTECT_OUT_PARAM(1, protect_param(ctx, 0) * 4); return true;
    case SYS_sched_getaffinity: PROTECT_OUT_PARAM(2, protect_param(ctx, 1)); return true;
    case SYS_sched_getparam: PROTECT_OUT_PARAM(1, 4); return true;
    case SYS_sched_rr_get_interval: PROTECT_OUT_PARAM(1, 16); return true;
    case SYS_getcpu: PROTECT_OUT_PARAM(0, 4); PROTECT_OUT_PARAM(1, 4); return true;
    case SYS_getitimer: PROTECT_OUT_PARAM(1, 32); return true;
    case SYS_setitimer: PROTECT_OUT_PARAM(2, 32); return true;
    case SYS_wait4: PROTECT_OUT_PARAM(1, 4); PROTECT_OUT_PARAM(3, 144); return true;
    case SYS_waitid: PROTECT_OUT_PARAM(2, 128); PROTECT_OUT_PARAM(4, 144); return true;
    case SYS_futex: return protect_futex(ctx);
    case SYS_set_tid_address:
      if (protect_overlaps(protect_param(ctx, 0), sizeof(int)))
        integrity_violation(ctx, "kernel_pointer");
      return true;
#ifdef SYS_rseq
    case SYS_rseq:
      if (protect_overlaps(protect_param(ctx, 0), protect_param(ctx, 1) ? protect_param(ctx, 1) : 32))
        integrity_violation(ctx, "kernel_pointer");
      return true;
#endif
    case SYS_set_robust_list:
      /* The kernel would rewrite futex words that the list, which the
       * application can change at any time, names at thread exit. Report
       * success without registering it, so libc takes the same path. */
      if (protect_overlaps(protect_param(ctx, 0), 24))
        integrity_violation(ctx, "kernel_pointer");
      dr_syscall_set_result(ctx, 0);
      return false;
    case SYS_arch_prctl:
      switch (protect_param(ctx, 0)) {
        case 0x1001: case 0x1002: case 0x1011: return true;
        case 0x1003: case 0x1004: case 0x1021: case 0x1022: PROTECT_OUT_PARAM(1, 8); return true;
        default: return protect_deny(ctx, EINVAL);
      }
    case SYS_prctl: return protect_prctl(ctx);
    case SYS_clone:
      return protect_clone_flags(ctx, protect_param(ctx, 0), protect_param(ctx, 2),
                                 protect_param(ctx, 3));
#ifdef SYS_clone3
    case SYS_clone3: {
      reg_t size = protect_param(ctx, 1);
      if (size < 64 || size > sizeof(d->protect_clone_args))
        return protect_deny(ctx, size < 64 ? EINVAL : E2BIG);
      memset(d->protect_clone_args, 0, sizeof(d->protect_clone_args));
      if (!dr_safe_read((void *)protect_param(ctx, 0), size, d->protect_clone_args, NULL))
        return protect_deny(ctx, EFAULT);
      const uint64_t *args = d->protect_clone_args;
      if (!protect_clone_flags(ctx, args[0], args[3], args[2]))
        return false;
      if (args[0] & UINT64_C(0x00001000))
        PROTECT_OUT(args[1], sizeof(int));
      if (args[5] && protect_overlaps(args[5], args[6] ? args[6] : 1))
        integrity_violation(ctx, "kernel_pointer");
      protect_rewrite(d, 0, d->protect_clone_args);
      return true;
    }
#endif
    case SYS_ptrace: integrity_violation(ctx, "ptrace"); return false;
    case SYS_process_vm_writev: integrity_violation(ctx, "process_vm_writev"); return false;
    case SYS_userfaultfd: integrity_violation(ctx, "userfaultfd"); return false;
    case SYS_perf_event_open: integrity_violation(ctx, "perf_event_open"); return false;
    case SYS_bpf: integrity_violation(ctx, "bpf"); return false;
    case SYS_seccomp: integrity_violation(ctx, "seccomp"); return false;
    case SYS_modify_ldt: case SYS_set_thread_area:
      integrity_violation(ctx, "segment_register");
      return false;
#ifdef SYS_io_uring_setup
    case SYS_io_uring_setup: case SYS_io_uring_enter: case SYS_io_uring_register:
      integrity_violation(ctx, "io_uring");
      return false;
#endif
    case SYS_shmat:
      if (protect_overlaps(protect_param(ctx, 1), 1) || (protect_param(ctx, 2) & SHM_REMAP))
        integrity_violation(ctx, "memory_map");
      return protect_deny(ctx, EPERM);
    case SYS_unlink: case SYS_rmdir: case SYS_truncate: case SYS_mkdir: case SYS_chmod:
    case SYS_chown: case SYS_lchown: case SYS_mknod: case SYS_utime: case SYS_utimes:
    case SYS_rename: case SYS_link: case SYS_symlink:
      protect_check_path(ctx, protect_param(ctx, 0));
      if (number == SYS_rename || number == SYS_link || number == SYS_symlink)
        protect_check_path(ctx, protect_param(ctx, 1));
      return protect_deny(ctx, EPERM);
    case SYS_unlinkat: case SYS_mkdirat: case SYS_fchmodat: case SYS_fchownat: case SYS_mknodat:
    case SYS_utimensat: case SYS_renameat: case SYS_linkat: case SYS_symlinkat:
#ifdef SYS_renameat2
    case SYS_renameat2:
#endif
      protect_check_path(ctx, protect_param(ctx, 1));
      if (number == SYS_renameat || number == SYS_linkat
#ifdef SYS_renameat2
          || number == SYS_renameat2
#endif
      )
        protect_check_path(ctx, protect_param(ctx, 3));
      if (number == SYS_symlinkat)
        protect_check_path(ctx, protect_param(ctx, 2));
      return protect_deny(ctx, EPERM);
    case SYS_ftruncate: case SYS_fallocate: case SYS_fchmod: case SYS_fchown: case SYS_flock:
      PROTECT_FD(0);
      return protect_deny(ctx, EPERM);
    case SYS_sendfile: case SYS_splice: case SYS_tee: case SYS_copy_file_range:
    case SYS_vmsplice:
      PROTECT_FD(number == SYS_splice ? 2 : 0);
      return protect_deny(ctx, EPERM);
    default:
      return protect_deny(ctx, EPERM);
  }
}

/* An opened descriptor must not reach the records, the live page, process
 * memory through procfs, or raw devices. */
static void protect_check_opened(void *ctx, int fd, int flags) {
  struct stat st;
  if (fstat(fd, &st) != 0) {
    close(fd);
    integrity_violation(ctx, "protected_file");
  }
  const char *vector = NULL;
  if (S_ISBLK(st.st_mode) ||
      (S_ISCHR(st.st_mode) && major(st.st_rdev) == 1 && minor(st.st_rdev) != 3 &&
       minor(st.st_rdev) != 5 && minor(st.st_rdev) != 7 && minor(st.st_rdev) != 8 &&
       minor(st.st_rdev) != 9))
    vector = "device";
  if (live_work && st.st_dev == live_dev && st.st_ino == live_ino)
    vector = "protected_file";
  if (!vector && (uint64_t)st.st_dev == protect_outdir_dev) {
    char link[64], path[sizeof(outdir) + 8];
    dr_snprintf(link, sizeof(link), "/proc/self/fd/%d", fd);
    ssize_t length = readlink(link, path, sizeof(path) - 1);
    if (length < 0)
      vector = "protected_file";
    else {
      path[length] = '\0';
      if ((size_t)length > protect_outdir_length &&
          strncmp(path, outdir, protect_outdir_length) == 0 && path[protect_outdir_length] == '/')
        vector = "protected_file";
    }
  }
  if (!vector) {
    struct statfs fs;
    if (fstatfs(fd, &fs) != 0)
      vector = "protected_file";
    else if (fs.f_type == PROC_SUPER_MAGIC_VALUE && (flags & O_ACCMODE) != O_RDONLY &&
             !(flags & O_PATH))
      vector = "proc_write";
  }
  if (vector) {
    close(fd);
    integrity_violation(ctx, vector);
  }
}

static void protect_post_syscall(void *ctx, thread_data_t *d) {
  ptr_int_t result = (ptr_int_t)dr_syscall_get_result(ctx);
  if (d->protect_param >= 0) {
    static const reg_id_t params[] = {DR_REG_RDI, DR_REG_RSI, DR_REG_RDX,
                                      DR_REG_R10, DR_REG_R8,  DR_REG_R9};
    dr_mcontext_t mc = {.size = sizeof(mc), .flags = DR_MC_INTEGER};
    if (!dr_get_mcontext(ctx, &mc))
      fail("meter protection: cannot restore a syscall parameter");
    reg_set_value(params[d->protect_param], &mc, d->protect_param_saved);
    if (!dr_set_mcontext(ctx, &mc))
      fail("meter protection: cannot restore a syscall parameter");
    d->protect_param = -1;
  }
  if (d->protect_open && result >= 0)
    protect_check_opened(ctx, (int)result, d->protect_open_flags);
  if (d->protect_map && result > 0 && (ptr_uint_t)result < PROTECT_USER_TOP &&
      protect_overlaps((ptr_uint_t)result, 1))
    integrity_violation(ctx, "memory_map");
  d->protect_open = d->protect_map = false;
}

/* Apply a validated parameter copy once every other pre-syscall check passed. */
static void protect_apply_rewrite(void *ctx, thread_data_t *d) {
  if (d->protect_param < 0)
    return;
  d->protect_param_saved = dr_syscall_get_param(ctx, d->protect_param);
  dr_syscall_set_param(ctx, d->protect_param, d->protect_param_value);
}

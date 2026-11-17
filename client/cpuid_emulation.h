/* DynamoRIO glue for cpuid_profile.h. Include dr_api.h and drmgr.h first.
 *
 * An app2app pass replaces each application CPUID/XGETBV with a drmgr
 * emulation region: start label, clean call, one application NOP carrying the
 * original translation, end label. Observational passes that honor emulation
 * labels (such as Durante's basic-block counter) charge the region exactly
 * once as the original instruction. The original instruction is owned by the
 * start label after drmgr_insert_emulation_start(), so it is unlinked here but
 * not destroyed. */
#ifndef DURANTE_CPUID_EMULATION_H
#define DURANTE_CPUID_EMULATION_H

#include "cpuid_profile.h"
#include <cpuid.h>

static enum durante_cpuid_profile durante_cpuid_active;
/* Called from the clean call when the request cannot be represented. The
 * embedding client must make the measurement unusable; the instruction then
 * returns zeros. */
static void (*durante_cpuid_invalid)(void *drcontext);

/* Validate the real processor once, before any application instruction runs.
 * Returns NULL on success, otherwise a static reason. */
static const char *durante_cpuid_enable(enum durante_cpuid_profile profile,
                                        void (*invalid)(void *)) {
  unsigned int eax = 0, ebx = 0, ecx = 0, edx = 0;
  unsigned int ext_ecx = 0, ext_edx = 0;
  if (!invalid)
    return "missing CPUID invalidation callback";
  unsigned int max_extended = __get_cpuid_max(0x80000000u, NULL);
  if (!__get_cpuid(1, &eax, &ebx, &ecx, &edx))
    return "host CPUID leaf 1 unavailable";
  if (max_extended >= 0x80000001u &&
      !__get_cpuid(0x80000001u, &eax, &ebx, &ext_ecx, &ext_edx))
    return "host CPUID leaf 0x80000001 unavailable";
  const char *missing = durante_cpuid_host_missing(profile, max_extended, ecx, edx,
                                                   ext_ecx, ext_edx);
  if (missing)
    return missing;
  durante_cpuid_active = profile;
  durante_cpuid_invalid = invalid;
  return NULL;
}

static void durante_cpuid_emulate(int is_xgetbv) {
  void *ctx = dr_get_current_drcontext();
  dr_mcontext_t mc = {0};
  mc.size = sizeof(mc);
  mc.flags = DR_MC_INTEGER;
  if (!dr_get_mcontext(ctx, &mc)) {
    durante_cpuid_invalid(ctx);
    return;
  }
  if (is_xgetbv) {
    uint32_t eax = 0, edx = 0;
    if (!durante_xgetbv_lookup(durante_cpuid_active, (uint32_t)mc.xcx, &eax, &edx)) {
      durante_cpuid_invalid(ctx);
      eax = edx = 0;
    }
    /* 32-bit destination writes zero the upper register halves. */
    mc.xax = eax;
    mc.xdx = edx;
  } else {
    uint32_t out[4];
    if (!durante_cpuid_lookup(durante_cpuid_active, (uint32_t)mc.xax,
                              (uint32_t)mc.xcx, out))
      durante_cpuid_invalid(ctx);
    mc.xax = out[0];
    mc.xbx = out[1];
    mc.xcx = out[2];
    mc.xdx = out[3];
  }
  if (!dr_set_mcontext(ctx, &mc))
    durante_cpuid_invalid(ctx);
}

/* Register with drmgr_register_bb_app2app_event(). Translation (state
 * recreation) passes produce the same list. */
static dr_emit_flags_t durante_cpuid_app2app(void *drcontext, void *tag,
                                             instrlist_t *bb, bool for_trace,
                                             bool translating) {
  (void)tag;
  (void)for_trace;
  (void)translating;
  if (durante_cpuid_active == DURANTE_CPUID_NONE)
    return DR_EMIT_DEFAULT;
  instr_t *next;
  for (instr_t *ins = instrlist_first(bb); ins != NULL; ins = next) {
    next = instr_get_next(ins);
    if (!instr_is_app(ins))
      continue;
    int opcode = instr_get_opcode(ins);
    if (opcode != OP_cpuid && opcode != OP_xgetbv)
      continue;
    app_pc pc = instr_get_app_pc(ins);
    emulated_instr_t emulated;
    memset(&emulated, 0, sizeof(emulated));
    emulated.size = sizeof(emulated);
    emulated.pc = pc;
    emulated.instr = ins;
    emulated.flags = 0;
    if (!drmgr_insert_emulation_start(drcontext, bb, ins, &emulated)) {
      dr_fprintf(STDERR, "durante: incompatible drmgr emulation API\n");
      dr_abort();
    }
    dr_insert_clean_call_ex(drcontext, bb, ins, (void *)durante_cpuid_emulate,
                            DR_CLEANCALL_READS_APP_CONTEXT |
                                DR_CLEANCALL_WRITES_APP_CONTEXT,
                            1, OPND_CREATE_INT32(opcode == OP_xgetbv ? 1 : 0));
    /* Keep one application instruction in the list so a block that consists
     * only of the replaced instruction still has an application translation. */
    instr_t *nop = INSTR_CREATE_nop(drcontext);
    instr_set_translation(nop, pc);
    instrlist_preinsert(bb, ins, nop);
    drmgr_insert_emulation_end(drcontext, bb, ins);
    instrlist_remove(bb, ins);
  }
  return DR_EMIT_DEFAULT;
}

#endif

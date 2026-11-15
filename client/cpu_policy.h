/* Native instruction policy for the pinned DynamoRIO x86-64 decoder.
 * Include dr_api.h first. This is an in-process diagnostic check, not a
 * tamper-resistant execution boundary. Keep it aligned with aonohako's
 * internal/cpupolicy object checker. */
#ifndef DURANTE_CPU_POLICY_H
#define DURANTE_CPU_POLICY_H

enum cpu_policy_decision {
  CPU_POLICY_ALLOW, CPU_POLICY_AVX, CPU_POLICY_SIMD, CPU_POLICY_UNDECODED
};

static bool cpu_policy_scalar_xmm(instr_t *instruction)
{
  switch (instr_get_opcode(instruction)) {
  case OP_movss: case OP_movsd: case OP_movd: case OP_movq:
  case OP_movaps: case OP_movapd: case OP_movups: case OP_movupd:
  case OP_movdqa: case OP_movdqu:
  case OP_addss: case OP_addsd: case OP_subss: case OP_subsd:
  case OP_mulss: case OP_mulsd: case OP_divss: case OP_divsd:
  case OP_minss: case OP_minsd: case OP_maxss: case OP_maxsd:
  case OP_sqrtss: case OP_sqrtsd: case OP_rsqrtss: case OP_rcpss:
  case OP_roundss: case OP_roundsd:
  case OP_comiss: case OP_comisd: case OP_ucomiss: case OP_ucomisd:
  case OP_cvtss2sd: case OP_cvtsd2ss: case OP_cvtsi2ss: case OP_cvtsi2sd:
  case OP_cvtss2si: case OP_cvtsd2si: case OP_cvttss2si: case OP_cvttsd2si:
  case OP_cmpss: case OP_cmpsd:
    return true;
  case OP_pxor: case OP_xorps: case OP_xorpd: {
    /* The IR represents legacy destructive XOR as one destination and two
     * sources. Only same-register XMM zeroing is an allowed scalar idiom. */
    if (instr_num_dsts(instruction) != 1 || instr_num_srcs(instruction) != 2)
      return false;
    opnd_t dst = instr_get_dst(instruction, 0);
    if (!opnd_is_reg(dst) || !reg_is_strictly_xmm(opnd_get_reg(dst)))
      return false;
    for (int i = 0; i < 2; ++i) {
      opnd_t src = instr_get_src(instruction, i);
      if (!opnd_is_reg(src) || opnd_get_reg(src) != opnd_get_reg(dst))
        return false;
    }
    return true;
  }
  default:
    return false;
  }
}

static bool cpu_policy_avx_opcode(instr_t *instruction)
{
  int opcode = instr_get_opcode(instruction);
  switch (opcode) {
  /* These canonical opcode names also begin with v, but are not AVX. */
  case OP_verr: case OP_verw: case OP_vmcall: case OP_vmclear: case OP_vmfunc:
  case OP_vmlaunch: case OP_vmptrld: case OP_vmptrst: case OP_vmread:
  case OP_vmresume: case OP_vmwrite: case OP_vmxoff: case OP_vmxon:
  case OP_vmrun: case OP_vmmcall: case OP_vmload: case OP_vmsave:
    return false;
  default: {
    /* A decoder opcode name is independent of disassembly syntax, operand
     * text, symbol names and prefixes. It also covers operand-free VZERO*. */
    const char *name = decode_opcode_name(opcode);
    return (name && name[0] == 'v') || instr_is_opmask(instruction);
  }
  }
}

static enum cpu_policy_decision cpu_policy_classify(instr_t *instruction,
                                                    bool deny_avx,
                                                    bool deny_simd)
{
  if (!deny_avx && !deny_simd)
    return CPU_POLICY_ALLOW;
  /* The pinned decoder has no AMX/TMM support. Never treat a decoder failure
   * or raw, undecodable application instruction as an allowed scalar opcode. */
  int opcode = instr_get_opcode(instruction);
  if (opcode == OP_INVALID || opcode == OP_UNDECODED)
    return CPU_POLICY_UNDECODED;
  bool avx = cpu_policy_avx_opcode(instruction);
  bool xmm = false, mmx = false;
  for (int side = 0; side < 2; ++side) {
    int count = side ? instr_num_dsts(instruction) : instr_num_srcs(instruction);
    for (int i = 0; i < count; ++i) {
      opnd_t operand = side ? instr_get_dst(instruction, i)
                            : instr_get_src(instruction, i);
      for (int j = 0; j < opnd_num_regs_used(operand); ++j) {
        reg_id_t reg = opnd_get_reg_used(operand, j);
        avx = avx || reg_is_strictly_ymm(reg) || reg_is_strictly_zmm(reg) ||
              reg_is_opmask(reg);
        xmm = xmm || reg_is_strictly_xmm(reg);
        mmx = mmx || reg_is_mmx(reg);
      }
    }
  }
  if (avx)
    return deny_avx ? CPU_POLICY_AVX : CPU_POLICY_SIMD;
  if (!deny_simd)
    return CPU_POLICY_ALLOW;
  if (mmx || opcode == OP_emms || opcode == OP_femms ||
      (xmm && !cpu_policy_scalar_xmm(instruction)))
    return CPU_POLICY_SIMD;
  return CPU_POLICY_ALLOW;
}
#endif

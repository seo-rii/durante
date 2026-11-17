/* Fixed virtual CPU identification for cross-host repeatability.
 *
 * Instruction counts depend on which implementation a program selects after
 * reading CPUID. glibc, libstdc++, OpenSSL, BLAS libraries, JITs and language
 * runtimes all dispatch on it, so two hosts with different processors execute
 * different code for identical input. A profile replaces every application
 * CPUID result with one constant table and XGETBV with a constant XCR0. The
 * result never depends on the host, except that a host lacking any reported
 * instruction-set feature is rejected before the application starts.
 *
 * This header has no DynamoRIO dependency so the table can be unit tested.
 * Define bool first: include dr_api.h (whose bool is char) or stdbool.h.
 * It only masks capabilities: every reported ISA bit must exist on the host.
 * The profiles report OSXSAVE=0, so no XSAVE area size is ever advertised;
 * software therefore uses FXSAVE paths, which are identical on every host. */
#ifndef DURANTE_CPUID_PROFILE_H
#define DURANTE_CPUID_PROFILE_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define DURANTE_CPUID_PROFILE_VERSION 1

enum durante_cpuid_profile {
  DURANTE_CPUID_NONE = 0,
  DURANTE_CPUID_X86_64_V1 = 1,
  DURANTE_CPUID_X86_64_V2 = 2,
};

/* Leaf 1 EDX: FPU, TSC, CX8, CMOV, CLFSH, MMX, FXSR, SSE, SSE2. */
#define DURANTE_CPUID_1_EDX UINT32_C(0x07888111)
/* Leaf 1 ECX for x86-64-v2: SSE3, SSSE3, CX16, SSE4.1, SSE4.2, POPCNT. */
#define DURANTE_CPUID_1_ECX_V2 UINT32_C(0x00982201)
/* Extended leaf 1 EDX: SYSCALL, NX, LM. */
#define DURANTE_CPUID_EXT1_EDX UINT32_C(0x20100800)
/* Extended leaf 1 ECX for x86-64-v2: LAHF/SAHF in 64-bit mode. */
#define DURANTE_CPUID_EXT1_ECX_V2 UINT32_C(0x00000001)
/* Family 6, model 0, stepping 0: no real model, so no model quirk applies. */
#define DURANTE_CPUID_SIGNATURE UINT32_C(0x00000600)
/* CLFLUSH line size 8 * 8 = 64 bytes, one logical processor, APIC ID 0. */
#define DURANTE_CPUID_1_EBX UINT32_C(0x00010800)
#define DURANTE_CPUID_MAX_BASIC UINT32_C(0x00000007)
#define DURANTE_CPUID_MAX_EXTENDED UINT32_C(0x80000008)
/* 48 physical and 48 linear address bits: four-level paging. */
#define DURANTE_CPUID_ADDRESS_SIZES UINT32_C(0x00003030)
/* XCR0 with only x87 and SSE state; reported XSAVE support is absent. */
#define DURANTE_XCR0 UINT32_C(0x00000003)

/* A registered vendor ID is required: glibc does not read leaf 1 for an
 * unregistered vendor, so its ISA-level check rejects libc itself ("CPU ISA
 * level is lower than required"). The signature (family 6, model 0) matches no
 * real model, so model-specific tuning tables are not selected; the brand
 * string identifies the virtual profile. Cache descriptor leaves 2 and 4 are
 * empty, so cache-size heuristics see the same zero values on every host. */
static const char durante_cpuid_vendor[13] = "GenuineIntel";

static const char *durante_cpuid_profile_name(enum durante_cpuid_profile p) {
  switch (p) {
  case DURANTE_CPUID_X86_64_V1:
    return "x86-64-v1";
  case DURANTE_CPUID_X86_64_V2:
    return "x86-64-v2";
  default:
    return NULL;
  }
}

static enum durante_cpuid_profile durante_cpuid_profile_parse(const char *name) {
  if (name && strcmp(name, "x86-64-v1") == 0)
    return DURANTE_CPUID_X86_64_V1;
  if (name && strcmp(name, "x86-64-v2") == 0)
    return DURANTE_CPUID_X86_64_V2;
  return DURANTE_CPUID_NONE;
}

static uint32_t durante_cpuid_word(const char *text) {
  return (uint32_t)(unsigned char)text[0] | (uint32_t)(unsigned char)text[1] << 8 |
         (uint32_t)(unsigned char)text[2] << 16 |
         (uint32_t)(unsigned char)text[3] << 24;
}

/* Fill out[EAX, EBX, ECX, EDX] for one CPUID request. Unlisted leaves, every
 * subleaf of a fixed leaf other than its defined one, hypervisor leaves and
 * out-of-range requests return zeros. The result is a pure function of the
 * profile, leaf and subleaf. Returns false only for an unknown profile. */
static bool durante_cpuid_lookup(enum durante_cpuid_profile p, uint32_t leaf,
                                 uint32_t subleaf, uint32_t out[4]) {
  out[0] = out[1] = out[2] = out[3] = 0;
  if (p != DURANTE_CPUID_X86_64_V1 && p != DURANTE_CPUID_X86_64_V2)
    return false;
  bool v2 = p == DURANTE_CPUID_X86_64_V2;
  /* 48 bytes: CPUID leaves 0x80000002-0x80000004, NUL padded. */
  char brand[48];
  memset(brand, 0, sizeof(brand));
  const char *label = v2 ? "Durante virtual CPU x86-64-v2" : "Durante virtual CPU x86-64-v1";
  memcpy(brand, label, strlen(label));
  switch (leaf) {
  case 0:
    out[0] = DURANTE_CPUID_MAX_BASIC;
    out[1] = durante_cpuid_word(durante_cpuid_vendor);
    out[3] = durante_cpuid_word(durante_cpuid_vendor + 4);
    out[2] = durante_cpuid_word(durante_cpuid_vendor + 8);
    break;
  case 1:
    out[0] = DURANTE_CPUID_SIGNATURE;
    out[1] = DURANTE_CPUID_1_EBX;
    out[2] = v2 ? DURANTE_CPUID_1_ECX_V2 : 0;
    out[3] = DURANTE_CPUID_1_EDX;
    break;
  case 0x80000000u:
    out[0] = DURANTE_CPUID_MAX_EXTENDED;
    break;
  case 0x80000001u:
    out[2] = v2 ? DURANTE_CPUID_EXT1_ECX_V2 : 0;
    out[3] = DURANTE_CPUID_EXT1_EDX;
    break;
  case 0x80000002u:
  case 0x80000003u:
  case 0x80000004u: {
    const char *part = brand + (leaf - 0x80000002u) * 16;
    for (int i = 0; i < 4; ++i)
      out[i] = durante_cpuid_word(part + i * 4);
    break;
  }
  case 0x80000008u:
    out[0] = DURANTE_CPUID_ADDRESS_SIZES;
    break;
  default:
    /* Leaves 2-7 (including structured extended features) and every other
     * range report no capability. */
    break;
  }
  (void)subleaf;
  return true;
}

/* XGETBV result for ECX 0 (XCR0) and 1 (XINUSE). Other indexes are not
 * defined by the profile; the caller must invalidate the measurement. */
static bool durante_xgetbv_lookup(enum durante_cpuid_profile p, uint32_t index,
                                  uint32_t *eax, uint32_t *edx) {
  if ((p != DURANTE_CPUID_X86_64_V1 && p != DURANTE_CPUID_X86_64_V2) ||
      (index != 0 && index != 1))
    return false;
  *eax = DURANTE_XCR0;
  *edx = 0;
  return true;
}

/* Host requirements: every reported instruction-set bit must be present.
 * Inputs are the host's own leaf 1 and extended leaf 1 ECX/EDX values and its
 * maximum extended leaf. Returns NULL when the host qualifies. */
static const char *durante_cpuid_host_missing(enum durante_cpuid_profile p,
                                              uint32_t max_extended,
                                              uint32_t leaf1_ecx, uint32_t leaf1_edx,
                                              uint32_t ext1_ecx, uint32_t ext1_edx) {
  if (p != DURANTE_CPUID_X86_64_V1 && p != DURANTE_CPUID_X86_64_V2)
    return "unknown CPUID profile";
  uint32_t want[4];
  durante_cpuid_lookup(p, 1, 0, want);
  if ((leaf1_edx & want[3]) != want[3])
    return "host lacks a leaf 1 EDX feature reported by the CPUID profile";
  if ((leaf1_ecx & want[2]) != want[2])
    return "host lacks a leaf 1 ECX feature reported by the CPUID profile";
  if (max_extended < 0x80000001u)
    return "host lacks extended CPUID leaf 0x80000001";
  durante_cpuid_lookup(p, 0x80000001u, 0, want);
  if ((ext1_edx & want[3]) != want[3])
    return "host lacks an extended EDX feature reported by the CPUID profile";
  if ((ext1_ecx & want[2]) != want[2])
    return "host lacks an extended ECX feature reported by the CPUID profile";
  return NULL;
}

#endif

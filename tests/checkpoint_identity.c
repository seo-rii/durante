/* Generated code whose blocks exercise each settling of a packed write
 * checkpoint (tests/checkpoint_identity.py):
 *
 *   selfwrite  stores into the immediate of a later instruction of its own
 *              block, on an RWX page, so that a sandboxed copy ends at that
 *              write (a completed write);
 *   rep        a REP STOS into the code page's data, then a REPE CMPSB, which
 *              does not write, in the same block;
 *   far        a block that continues through a direct jump to code 256 KiB
 *              away, with a store on each side; the runtime can elide the
 *              jump, so the block's code spans more than a packed word names.
 *
 * Every call checks its result. Prints one JSON line.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

#define FAR_DISTANCE (256 << 10)

typedef int (*selfwrite_t)(int value);
typedef int (*rep_t)(uint8_t *data, int value);
typedef int (*far_t)(uint32_t *out, int value);

static uint8_t *code;
static size_t length = 2 * FAR_DISTANCE + (FAR_DISTANCE / 2);

static void emit(size_t *at, const void *bytes, size_t n) {
  memcpy(code + *at, bytes, n);
  *at += n;
}

int main(int argc, char **argv) {
  int rounds = argc > 1 ? atoi(argv[1]) : 2000;
  code = mmap(NULL, length, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANONYMOUS,
              -1, 0);
  if (code == MAP_FAILED)
    return 2;
  memset(code, 0xcc, length);

  /* selfwrite(value): mov byte [rip + 4], dil; xor eax, eax; add eax, imm8; ret
   * The store writes the imm8 of the add, 4 bytes after the store ends. */
  size_t at = 0;
  static const uint8_t selfwrite[] = {0x40, 0x88, 0x3d, 0x04, 0x00, 0x00, 0x00, /* mov [rip+4], dil */
                                      0x31, 0xc0,                               /* xor eax, eax */
                                      0x83, 0xc0, 0x00,                         /* add eax, imm8 */
                                      0xc3};
  emit(&at, selfwrite, sizeof(selfwrite));

  /* rep(data, value): fill 64 bytes of data with value, compare them with the
   * 64 bytes after, which the caller set equal, and return the number of
   * bytes left when they differ (0 when all match).
   *   mov eax, esi; mov rdi, rdi; mov ecx, 64; rep stosb
   *   lea rsi, [rdi - 64]; (rdi now data+64) ; mov ecx, 64; repe cmpsb
   *   mov eax, ecx; ret */
  size_t rep_at = 0x400;
  at = rep_at;
  static const uint8_t rep[] = {
      0x89, 0xf0,                   /* mov eax, esi */
      0x49, 0x89, 0xf8,             /* mov r8, rdi */
      0xb9, 0x40, 0x00, 0x00, 0x00, /* mov ecx, 64 */
      0xf3, 0xaa,                   /* rep stosb */
      0x4c, 0x89, 0xc6,             /* mov rsi, r8 */
      0xb9, 0x40, 0x00, 0x00, 0x00, /* mov ecx, 64 */
      0xf3, 0xa6,                   /* repe cmpsb: [rsi] (data) with [rdi] (data + 64) */
      0x89, 0xc8,                   /* mov eax, ecx */
      0xc3};
  emit(&at, rep, sizeof(rep));

  /* far(out, value): mov [rdi], esi; jmp +FAR; ...; mov [rdi + 4], esi;
   * lea eax, [rsi + 1]; ret */
  size_t far_at = FAR_DISTANCE / 2 + 0x800;
  at = far_at;
  static const uint8_t far_head[] = {0x89, 0x37}; /* mov [rdi], esi */
  emit(&at, far_head, sizeof(far_head));
  size_t far_target = far_at + FAR_DISTANCE;
  int32_t rel = (int32_t)(far_target - (at + 5));
  uint8_t jmp[5] = {0xe9};
  memcpy(jmp + 1, &rel, 4);
  emit(&at, jmp, sizeof(jmp));
  at = far_target;
  static const uint8_t far_tail[] = {0x89, 0x77, 0x04, /* mov [rdi + 4], esi */
                                     0x8d, 0x46, 0x01, /* lea eax, [rsi + 1] */
                                     0xc3};
  emit(&at, far_tail, sizeof(far_tail));

  /* The far function's pages are read-only code, as a library's text: the
   * runtime follows direct jumps there without page-by-page tracking. */
  if (mprotect(code + FAR_DISTANCE / 2, length - FAR_DISTANCE / 2, PROT_READ | PROT_EXEC) != 0)
    return 2;
  selfwrite_t call_selfwrite = (selfwrite_t)(void *)code;
  rep_t call_rep = (rep_t)(void *)(code + rep_at);
  far_t call_far = (far_t)(void *)(code + far_at);
  uint8_t *data = code + 0x600; /* on the page of rep, after its code */
  long bad = 0;
  for (int i = 0; i < rounds; ++i) {
    int value = i % 100 + 1;
    if (call_selfwrite(value) != value)
      ++bad;
    memset(data + 64, value, 64);
    if (call_rep(data, value) != 0 || data[0] != value || data[63] != value)
      ++bad;
    data[64 + i % 64] = (uint8_t)(value + 1); /* one byte differs */
    if (call_rep(data, value) != 63 - i % 64)
      ++bad;
    uint32_t out[2] = {0, 0};
    if (call_far(out, value) != value + 1 || out[0] != (uint32_t)value ||
        out[1] != (uint32_t)value)
      ++bad;
  }
  printf("{\"rounds\": %d, \"bad\": %ld}\n", rounds, bad);
  munmap(code, length);
  return bad != 0;
}

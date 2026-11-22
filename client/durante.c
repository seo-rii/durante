/*
 * Derived from DynamoRIO api/samples/inscount.cpp and countcalls.c.
 * Copyright (c) 2014-2023 Google, Inc. All rights reserved.
 * Copyright (c) 2011 Massachusetts Institute of Technology. All rights
 * reserved. Copyright (c) 2003-2010 VMware, Inc. All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * * Redistributions of source code must retain the above copyright notice,
 *   this list of conditions and the following disclaimer.
 * * Redistributions in binary form must reproduce the above copyright notice,
 *   this list of conditions and the following disclaimer in the documentation
 *   and/or other materials provided with the distribution.
 * * Neither the name of VMware, Inc. nor the names of its contributors may be
 *   used to endorse or promote products derived from this software without
 *   specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
 * AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 * ARE DISCLAIMED. IN NO EVENT SHALL VMWARE, INC. OR CONTRIBUTORS BE LIABLE FOR
 * ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 * DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
 * SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
 * CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
 * OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

#include "dr_api.h"
#include "drmgr.h"
#include "durante_runtime.h"
#include "cpu_policy.h"
#include "cpuid_emulation.h"
#include "latency_bound.h"
#ifdef DURANTE_TEST_BLOCK_LATENCY_BOUND
/* The state-function harness stubs only IR accessors and supplies bounds. */
static uint32_t DURANTE_TEST_BLOCK_LATENCY_BOUND(void *drcontext, void *tag, instrlist_t *bb,
                                                 uint instructions);
#define durante_block_latency_bound DURANTE_TEST_BLOCK_LATENCY_BOUND
#else
#define durante_block_latency_bound durante_latency_bound_x16
#endif
#include <elf.h>
#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <linux/mman.h>
#include <sys/shm.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
#ifndef X86_64
#error "durante supports Linux x86-64 only"
#endif
#define CLONE_THREAD_FLAG UINT64_C(0x00010000)
#define CLONE_VM_FLAG UINT64_C(0x00000100)
#define CLONE_VFORK_FLAG UINT64_C(0x00004000)
#define CLONE_FILES_FLAG UINT64_C(0x00000400)
#define CLONE_CLEAR_SIGHAND_FLAG UINT64_C(0x100000000)
#define X32_SYSCALL_BIT 0x40000000U
#define MAX_STREAM_BYTES (1024 * 1024)
#define MAX_EVENTS 4096

typedef struct {
  uint64_t instructions, counter_overflows, signals, rep_bytes, rep_entries,
      unsupported_rep_entries;
  uint64_t rep_movs_bytes, rep_stos_bytes, rep_movs_entries, rep_stos_entries;
  uint64_t published_instructions, published_movs, published_stos;
  uint64_t fast_until; /* Exclusive pre-charge threshold; zero forces the callback. */
  /* Flat inline accounting (no live clock): the value of instructions before
   * the most recent block entry, and its value just after a RETRY rollback. */
  uint64_t entry_base, restart_base;
  /* Live flat runs: the sampler's list of threads that publish entry_base. */
  void *sample_next, *sample_prev;
  byte *raw_tls_base; /* This thread's runtime segment base, for state restoration. */
  bool pending_stdin_read, received_stdin;
  bool rep_error;
  bool replay_error;
  /* A CPUID-profile request outside the fixed table (sticky invalidity). */
  bool cpuid_error;
  /* Optional static latency bound (latency_bound.h), in 1/16 cycles. The
   * current block's charge is kept so a replay can return its unexecuted
   * share; an unanalyzable block or underflow is sticky invalidity. */
  uint64_t latency_bound_x16;
  uint32_t block_instructions, block_bound_x16;
  uint32_t write_block_instructions, write_block_bound_x16;
  bool latency_error;
  app_pc write_pc, write_next_pc, restart_pc;
  uint64_t write_suffix, write_rep_request, restart_rep_request;
  int write_rep_opcode, write_rep_bytes, write_rep_count_reg;
  int restart_rep_opcode, restart_rep_bytes, restart_rep_count_reg;
  bool write_rep, restart_rep, restart_entered;
  bool pending_spawn, pending_exec, pending_unsupported;
  int pending_syscall;
  uint64_t pending_clone_flags;
  process_id_t child_pid;
  char child_segment[80];
  int child_file, child_exit_code;
  size_t child_bytes;
  uint64_t child_sequence;
  bool child_exit_seen, child_policy_exit;
  uint64_t memory_operand_bytes;
  uint32_t write_suffix_bytes, write_operand_bytes;
} thread_data_t;
static int tls_index;
/* In ordinary static code the generated accounting runs at every block and
 * store. Mirror the drmgr-owned pointer for a single segment-relative load. */
static bool raw_tls_enabled;
static reg_id_t raw_tls_register;
static uint raw_tls_offset;
/* Client-owned raw TLS slots: the accounting pointer and two scratch spills.
 * Private spill slots let restore_state() recognize exactly this client's
 * sequences; the runtime's shared spill slots are also used by its mangling. */
enum { RAW_TLS_STATE, RAW_TLS_SPILL_RCX, RAW_TLS_SPILL_R11, RAW_TLS_SLOTS };
/* Without a live clock nothing is published between syscalls. Every block and
 * store then uses flag-free inline accounting, including shared libraries and
 * generated code. Restart validation moves from block entry to the callbacks
 * that consume restart state (see settle_restart). */
static bool flat_inline;
static void *state_lock;
static int report_file = -1;
static process_id_t owner_pid;
static char outdir[4096], segment[80];
static uint64_t total_instructions, total_signals, total_rep_bytes,
    total_rep_entries, total_unsupported_rep_entries, threads_started,
    threads_ended, event_sequence;
static uint64_t total_rep_movs_bytes, total_rep_stos_bytes,
    total_rep_movs_entries, total_rep_stos_entries;
static bool rep_bytes_enabled, accounting_error, app_exit_seen, report_written;
static bool deny_avx, deny_simd;
static bool latency_bound_enabled, memory_operand_bytes_enabled;
static uint64_t total_latency_bound_x16, total_memory_operand_bytes;
static app_pc cpu_policy_exit_pc;
/* A replay callback must never do I/O or take a lock, even on a broken TLS
 * invariant.  This sticky process/shared-VM flag invalidates every snapshot. */
static int replay_without_tls;
/* Linux x86-64 little-endian ABI for -live_work_v1. The owner exclusively
 * creates a fresh, zero-filled file for one role before launch. All mappings of
 * a followed tree contribute to this one file, including across exec. */
typedef struct {
  uint64_t ready, invalid, instructions, movs, stos, overflow;
} live_work_t;
_Static_assert(sizeof(live_work_t) == 48 && __atomic_always_lock_free(8, 0),
               "live work requires lock-free aligned 64-bit counters");
static live_work_t *live_work;
/* With a live clock, flat inline blocks have no publication check at entry.
 * A client thread publishes each thread's completed-block count instead. */
static void *sample_lock;
static void *sample_head;
static bool live_sampler;
static int app_exit_code;
static size_t file_bytes;
#ifdef DURANTE_TEST_COUNT_LOCATIONS
#include "../tests/count_locations/profile.h"
#endif

#ifdef DURANTE_L1D_PROBE
static bool probe_l1d;
#endif

/* Only the original file-backed text of a static main executable is qualified
 * for the ordinary inline client. Unknown ELF layouts take the checked calls. */
static uintptr_t static_text_start, static_text_end, static_text_page_start;
static bool static_text_revoked;

static void find_static_text(const module_data_t *main) {
  Elf64_Ehdr elf;
  if (!main || !main->num_segments || main->segments[0].start != main->start ||
      main->segments[0].offset || !(main->segments[0].prot & DR_MEMPROT_READ) ||
      !dr_safe_read(main->start, sizeof(elf), &elf, NULL) ||
      memcmp(elf.e_ident, ELFMAG, SELFMAG) || elf.e_ident[EI_CLASS] != ELFCLASS64 ||
      elf.e_ident[EI_DATA] != ELFDATA2LSB || elf.e_machine != EM_X86_64 ||
      elf.e_type != ET_EXEC || elf.e_entry != (uintptr_t)main->entry_point ||
      elf.e_phentsize != sizeof(Elf64_Phdr) || !elf.e_phnum || elf.e_phnum > 128)
    return;
  size_t mapped = (uintptr_t)main->segments[0].end - (uintptr_t)main->start;
  if (elf.e_phoff > mapped || elf.e_phnum * sizeof(Elf64_Phdr) > mapped - elf.e_phoff)
    return;
  uintptr_t start = 0, end = 0;
  for (uint i = 0; i < elf.e_phnum; ++i) {
    Elf64_Phdr ph;
    if (!dr_safe_read(main->start + elf.e_phoff + i * sizeof(ph), sizeof(ph), &ph, NULL) ||
        ph.p_type == PT_INTERP || ph.p_type == PT_DYNAMIC)
      return;
    if (ph.p_type == PT_LOAD && ph.p_flags == (PF_R | PF_X) && ph.p_filesz <= ph.p_memsz &&
        ph.p_filesz <= UINTPTR_MAX - ph.p_vaddr && elf.e_entry >= ph.p_vaddr &&
        elf.e_entry - ph.p_vaddr < ph.p_filesz) {
      start = ph.p_vaddr;
      end = start + ph.p_filesz;
    }
  }
  for (uint i = 0; start && i < main->num_segments; ++i) {
    const module_segment_data_t *seg = &main->segments[i];
    if (seg->prot == (DR_MEMPROT_READ | DR_MEMPROT_EXEC) &&
        start >= (uintptr_t)seg->start && end <= (uintptr_t)seg->end) {
      static_text_start = start;
      static_text_end = end;
      static_text_page_start = (uintptr_t)seg->start;
      return;
    }
  }
}

#ifndef DURANTE_TEST_COUNT_LOCATIONS
static bool static_text_site(app_pc pc) {
  uintptr_t addr = (uintptr_t)pc;
  return addr >= static_text_start && addr < static_text_end &&
         !__atomic_load_n(&static_text_revoked, __ATOMIC_ACQUIRE);
}
#endif

static bool overlaps_static_text(uintptr_t addr, size_t size) {
  return size && addr < static_text_end &&
         (addr >= static_text_page_start || size > static_text_page_start - addr);
}

/* Live mode already intercepts every syscall. Revoke before any attempted
 * replacement, including failed attempts: addresses can subsequently name
 * another mapping. The kernel rounds mapping changes to page boundaries. */
static bool changes_static_text(void *ctx, int number) {
  switch (number) {
    case SYS_mmap:
      if (!(dr_syscall_get_param(ctx, 3) & MAP_FIXED))
        return false;
      break;
    case SYS_mremap:
      if ((dr_syscall_get_param(ctx, 3) & MREMAP_FIXED) &&
          overlaps_static_text(dr_syscall_get_param(ctx, 4), dr_syscall_get_param(ctx, 2)))
        return true;
      break;
    case SYS_shmat:
      return (dr_syscall_get_param(ctx, 2) & SHM_REMAP) != 0;
    case SYS_munmap:
    case SYS_mprotect:
    case SYS_pkey_mprotect:
    case SYS_remap_file_pages:
      break;
    default:
      return false;
  }
  return overlaps_static_text(dr_syscall_get_param(ctx, 0), dr_syscall_get_param(ctx, 1));
}

static bool cache_probe_enabled(void) {
#ifdef DURANTE_L1D_PROBE
  return probe_l1d;
#else
  return false;
#endif
}

static inline void invalidate_live_work(void) {
  if (live_work)
    __atomic_store_n(&live_work->invalid, 1, __ATOMIC_RELEASE);
}

static inline void counter_error(bool *flag) {
  *flag = true;
  invalidate_live_work();
}

static void fail(const char *m) {
  invalidate_live_work();
  dr_fprintf(STDERR, "durante: %s\n", m);
  dr_abort();
}

static void open_live_work(const char *path) {
  if (path[0] != '/')
    fail("live work path must be absolute");
  /* Use openat: the runtime redirects libc open() for writable files. */
  int fd = openat(AT_FDCWD, path, O_RDWR | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
  struct stat st;
  if (fd < 0 || fstat(fd, &st) != 0 || !S_ISREG(st.st_mode) ||
      st.st_nlink != 1 || st.st_size != (off_t)sizeof(*live_work))
    fail("invalid live work file");
  size_t size = sizeof(*live_work);
  void *mapping = dr_map_file(fd, &size, 0, NULL, DR_MEMPROT_READ | DR_MEMPROT_WRITE, 0);
  close(fd);
  if (!mapping || size < sizeof(*live_work))
    fail("cannot map live work file");
  live_work = mapping;
}

/* Use process-shared atomics rather than inherited process totals. Only the
 * thread owning a TLS baseline adds a delta. There is no snapshot merging at
 * fork/exec and a failed exec can publish the same baseline again for free. */
static void add_live_delta(uint64_t *counter, uint64_t delta, uint64_t overflow_bit) {
  if (!delta)
    return;
  uint64_t old = __atomic_load_n(counter, __ATOMIC_RELAXED);
  for (;;) {
    bool overflow = delta > UINT64_MAX - old;
    uint64_t next = overflow ? UINT64_MAX : old + delta;
    if (__atomic_compare_exchange_n(counter, &old, next, false,
                                    __ATOMIC_RELEASE, __ATOMIC_RELAXED)) {
      if (overflow)
        __atomic_fetch_or(&live_work->overflow, overflow_bit, __ATOMIC_RELEASE);
      return;
    }
  }
}

enum { LIVE_WORK_BATCH = 4096 };

/* The owning thread and the sampler both advance published_instructions; the
 * compare-and-swap winner adds the delta, so no interval is published twice. */
static void publish_instruction_target(uint64_t *published, uint64_t target) {
  uint64_t old = __atomic_load_n(published, __ATOMIC_ACQUIRE);
  while (target > old) {
    if (__atomic_compare_exchange_n(published, &old, target, false, __ATOMIC_ACQ_REL,
                                    __ATOMIC_ACQUIRE)) {
      add_live_delta(&live_work->instructions, target - old, 1);
      return;
    }
  }
}

static bool invalid_thread_work(const thread_data_t *d) {
  return d->signals || d->rep_error || d->replay_error || d->cpuid_error ||
         d->counter_overflows || d->unsupported_rep_entries;
}

static void publish_live_work(thread_data_t *d, bool flush) {
  uint64_t published = __atomic_load_n(&d->published_instructions, __ATOMIC_ACQUIRE);
  if (invalid_thread_work(d) || d->instructions < published ||
      d->rep_movs_bytes < d->published_movs || d->rep_stos_bytes < d->published_stos) {
    invalidate_live_work();
    return;
  }
  if (d->restart_pc) {
    if (flush)
      invalidate_live_work();
    return;
  }
  uint64_t instructions = d->instructions - published;
  uint64_t movs = d->rep_movs_bytes - d->published_movs;
  uint64_t stos = d->rep_stos_bytes - d->published_stos;
  /* Omit the current BB's speculative entry charge. Prior BBs have passed
   * the runtime's replay correction boundary. Flush small deltas at each
   * application syscall, including those that may block or replace the image. */
  if (!flush && (instructions | movs | stos) < LIVE_WORK_BATCH)
    return;
  publish_instruction_target(&d->published_instructions, d->instructions);
  add_live_delta(&live_work->movs, movs, 2);
  add_live_delta(&live_work->stos, stos, 4);
  d->published_movs = d->rep_movs_bytes;
  d->published_stos = d->rep_stos_bytes;
}

/* Flat inline blocks cannot run a callback at entry. Reconstruct what
 * charge_block() would have decided for those entries from two counter values:
 * no entry since the retry leaves the instruction count at restart_base, and
 * exactly one entry leaves entry_base at restart_base (every block charges at
 * least one instruction). Call before reading restart state. A non-REP retry
 * needs no first-block check for its counts: its unexecuted suffix was already
 * returned and the resumed block charges itself. */
static void settle_restart(thread_data_t *d) {
  if (!flat_inline || !d->restart_pc || d->instructions == d->restart_base)
    return;
  bool one_entry = d->entry_base == d->restart_base;
  if (d->restart_rep && one_entry) {
    d->restart_entered = true;
    return;
  }
  /* A REP restart must observe its REP in the first resumed block. */
  if (d->restart_rep)
    counter_error(&d->replay_error);
  d->restart_pc = NULL;
}

/* Settle the last flat charge before aggregation or replay. Later block
 * entries latch earlier wraps before overwriting entry_base. */
static void check_flat_counter(thread_data_t *d) {
  if (flat_inline && d->instructions < d->entry_base) {
    d->counter_overflows = 1;
    invalidate_live_work();
  }
}

/* Caller holds state_lock, except before application execution/fork resume. */
static void write_event(int fd, size_t *bytes, uint64_t *sequence,
                        process_id_t process, const char *image,
                        const char *kind, const char *fields) {
  char buffer[3072];
  if (++*sequence > MAX_EVENTS)
    fail("record count exceeded");
  int length = dr_snprintf(
      buffer, sizeof(buffer),
      "{\"schema\":1,\"client_version\":1,\"authority\":\"diagnostic_only\","
      "\"metric\":\"bb_entry_app_instructions\",\"pid\":%d,\"segment\":\"%s\","
      "\"seq\":%llu,\"event\":\"%s\"%s}\n",
      (int)process, image, (unsigned long long)*sequence, kind, fields);
  if (length <= 0 || (size_t)length >= sizeof(buffer) ||
      *bytes + (size_t)length > MAX_STREAM_BYTES)
    fail("record size exceeded");
  ssize_t n;
  do {
    n = dr_write_file(fd, buffer, (size_t)length);
  } while (n < 0 && errno == EINTR);
  if (n != length)
    fail("incomplete lifecycle write");
  *bytes += (size_t)length;
}
static void event(const char *kind, const char *fields) {
  write_event(report_file, &file_bytes, &event_sequence, owner_pid, segment,
              kind, fields);
}
static int open_pid_file(process_id_t process, size_t *bytes) {
  int dir = open(outdir, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
  if (dir < 0)
    fail("cannot open report directory without symlinks");
  char name[64];
  dr_snprintf(name, sizeof(name), "process-%d.jsonl", (int)process);
  int fd = openat(
      dir, name,
      O_WRONLY | O_CREAT | O_EXCL | O_APPEND | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK,
      0600);
  if (fd < 0 && errno == EEXIST)
    /* Validate the opened inode, but do not block before reaching fstat when a
     * target replaces its earlier image's stream pathname with a FIFO. */
    fd = openat(dir, name,
                O_WRONLY | O_APPEND | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
  close(dir);
  struct stat st;
  if (fd < 0 || fstat(fd, &st) != 0 || !S_ISREG(st.st_mode) ||
      st.st_nlink != 1 || st.st_uid != geteuid() || st.st_size < 0 ||
      st.st_size >= MAX_STREAM_BYTES)
    fail("unsafe or oversized report stream");
  /* Open the already validated inode through a kernel-owned fd alias, so
   * DynamoRIO reserves/protects the stream from application close/close_range.
   * Keep the no-follow/openat checks above: dr_open_file by pathname alone would
   * permit the application to redirect this write through a symlink. */
  char alias[64];
  dr_snprintf(alias, sizeof(alias), "/proc/self/fd/%d", fd);
  file_t protected_fd = dr_open_file(alias, DR_FILE_WRITE_APPEND);
  struct stat protected_st;
  if (protected_fd == INVALID_FILE || fstat(protected_fd, &protected_st) != 0 ||
      protected_st.st_dev != st.st_dev || protected_st.st_ino != st.st_ino)
    fail("cannot protect lifecycle stream");
  close(fd);
  *bytes = (size_t)st.st_size;
  return protected_fd;
}
static void child_event(thread_data_t *d, const char *kind,
                        const char *fields) {
  if (d->child_file < 0)
    d->child_file = open_pid_file(d->child_pid, &d->child_bytes);
  write_event(d->child_file, &d->child_bytes, &d->child_sequence, d->child_pid,
              d->child_segment, kind, fields);
}
static void route_event(thread_data_t *d, const char *kind,
                        const char *fields) {
  if (d && d->child_pid)
    child_event(d, kind, fields);
  else
    event(kind, fields);
}
static void start_record(thread_data_t *d, const char *origin,
                         process_id_t parent, const char *parent_segment) {
  char fields[512];
  const char *policy = deny_avx && deny_simd ? "avx,simd" : deny_avx ? "avx" : "simd";
  char policy_fields[96] = "";
  if (deny_avx || deny_simd)
    dr_snprintf(policy_fields, sizeof(policy_fields),
                ",\"cpu_policy_version\":1,\"cpu_policy\":\"%s\"", policy);
  char latency_fields[40] = "";
  if (latency_bound_enabled)
    dr_snprintf(latency_fields, sizeof(latency_fields), ",\"latency_bound_version\":%d",
                DURANTE_LATENCY_BOUND_VERSION);
  char cpuid_fields[96] = "";
  if (durante_cpuid_active != DURANTE_CPUID_NONE)
    dr_snprintf(cpuid_fields, sizeof(cpuid_fields),
                ",\"cpuid_profile_version\":%d,\"cpuid_profile\":\"%s\"",
                DURANTE_CPUID_PROFILE_VERSION,
                durante_cpuid_profile_name(durante_cpuid_active));
  dr_snprintf(fields, sizeof(fields),
              ",\"origin\":\"%s\",\"parent_pid\":%d,"
              "\"parent_segment\":\"%s\",\"rep_bytes_enabled\":%s,"
              "\"rep_breakdown_version\":1,\"sdk_"
              "version\":\"11.3.0\"%s%s%s%s",
              origin, (int)parent, parent_segment,
              rep_bytes_enabled ? "true" : "false", policy_fields, cpuid_fields,
              latency_fields, memory_operand_bytes_enabled ? ",\"memory_operand_bytes_version\":1" : "");
  route_event(d, "start", fields);
}
static void open_stream(const char *origin, process_id_t parent,
                        const char *parent_segment) {
  report_file = open_pid_file(owner_pid, &file_bytes);
  dr_snprintf(segment, sizeof(segment), "%d-%llu", (int)owner_pid,
              (unsigned long long)dr_get_microseconds());
  start_record(NULL, origin, parent, parent_segment);
}

static bool add_thread(thread_data_t *d) {
  if (d) {
    settle_restart(d);
    check_flat_counter(d);
  }
  if (!d || d->rep_error || d->replay_error || d->cpuid_error || d->restart_pc ||
      d->counter_overflows || d->latency_error ||
      UINT64_MAX - total_instructions < d->instructions ||
      UINT64_MAX - total_latency_bound_x16 < d->latency_bound_x16 ||
      UINT64_MAX - total_memory_operand_bytes < d->memory_operand_bytes ||
      UINT64_MAX - total_signals < d->signals ||
      UINT64_MAX - total_rep_bytes < d->rep_bytes ||
      UINT64_MAX - total_rep_entries < d->rep_entries ||
      UINT64_MAX - total_rep_movs_bytes < d->rep_movs_bytes ||
      UINT64_MAX - total_rep_stos_bytes < d->rep_stos_bytes ||
      UINT64_MAX - total_rep_movs_entries < d->rep_movs_entries ||
      UINT64_MAX - total_rep_stos_entries < d->rep_stos_entries ||
      UINT64_MAX - total_unsupported_rep_entries < d->unsupported_rep_entries)
    return false;
  total_instructions += d->instructions;
  total_latency_bound_x16 += d->latency_bound_x16;
  total_memory_operand_bytes += d->memory_operand_bytes;
  total_signals += d->signals;
  total_rep_bytes += d->rep_bytes;
  total_rep_entries += d->rep_entries;
  total_rep_movs_bytes += d->rep_movs_bytes;
  total_rep_stos_bytes += d->rep_stos_bytes;
  total_rep_movs_entries += d->rep_movs_entries;
  total_rep_stos_entries += d->rep_stos_entries;
  total_unsupported_rep_entries += d->unsupported_rep_entries;
  return true;
}

static void snapshot_event(const char *kind, void *ctx, bool is_exec) {
  thread_data_t *d = ctx ? drmgr_get_tls_field(ctx, tls_index) : NULL;
  if (d) {
    settle_restart(d);
    check_flat_counter(d);
  }
  bool child = d && d->child_pid;
  uint64_t instructions = child ? 0 : total_instructions,
           latency = child ? 0 : total_latency_bound_x16,
           operand_bytes = child ? 0 : total_memory_operand_bytes,
           signals = child ? 0 : total_signals,
           bytes = child ? 0 : total_rep_bytes,
           entries = child ? 0 : total_rep_entries,
           movs_bytes = child ? 0 : total_rep_movs_bytes,
           stos_bytes = child ? 0 : total_rep_stos_bytes,
           movs_entries = child ? 0 : total_rep_movs_entries,
           stos_entries = child ? 0 : total_rep_stos_entries,
           unsupported = child ? 0 : total_unsupported_rep_entries;
  uint64_t started = child ? 1 : threads_started,
           ended = child ? 0 : threads_ended;
  bool error = child ? false : accounting_error;
  error = error || __atomic_load_n(&replay_without_tls, __ATOMIC_RELAXED);
  if (is_exec || child) {
    error = error || !d || d->rep_error || d->replay_error || d->cpuid_error ||
            d->restart_pc || d->counter_overflows || d->latency_error ||
            started != ended + 1;
    if (d) {
      if (UINT64_MAX - instructions < d->instructions ||
          UINT64_MAX - latency < d->latency_bound_x16 ||
          UINT64_MAX - operand_bytes < d->memory_operand_bytes ||
          UINT64_MAX - signals < d->signals ||
          UINT64_MAX - bytes < d->rep_bytes ||
          UINT64_MAX - entries < d->rep_entries ||
          UINT64_MAX - movs_bytes < d->rep_movs_bytes ||
          UINT64_MAX - stos_bytes < d->rep_stos_bytes ||
          UINT64_MAX - movs_entries < d->rep_movs_entries ||
          UINT64_MAX - stos_entries < d->rep_stos_entries ||
          UINT64_MAX - unsupported < d->unsupported_rep_entries)
        error = true;
      else {
        instructions += d->instructions;
        latency += d->latency_bound_x16;
        operand_bytes += d->memory_operand_bytes;
        signals += d->signals;
        bytes += d->rep_bytes;
        entries += d->rep_entries;
        movs_bytes += d->rep_movs_bytes;
        stos_bytes += d->rep_stos_bytes;
        movs_entries += d->rep_movs_entries;
        stos_entries += d->rep_stos_entries;
        unsupported += d->unsupported_rep_entries;
      }
    }
    ++ended;
  }
  /* An interrupted block may have charged operands after the last instruction
   * that actually ran. Only the runtime's authenticated replay has a precise suffix. */
  if (memory_operand_bytes_enabled && signals)
    error = true;
  bool complete = started > 0 && started == ended;
  bool exit_seen = child ? d->child_exit_seen : app_exit_seen;
  int exit_code = child ? d->child_exit_code : app_exit_code;
  bool valid = !error && complete && instructions > 0 && (is_exec || exit_seen);
  char fields[1536];
  dr_snprintf(
      fields, sizeof(fields),
      ",\"instructions\":%llu,\"rep_bytes\":%llu,"
      "\"rep_entries\":%llu,\"rep_movs_bytes\":%llu,\"rep_stos_bytes\":%llu,"
      "\"rep_movs_entries\":%llu,\"rep_stos_entries\":%llu,"
      "\"unsupported_rep_entries\":%llu,\"signals_"
      "observed\":%llu,"
      "\"threads_started\":%llu,\"threads_finished\":%llu,\"threads_complete\":"
      "%s,"
      "\"accounting_error\":%s,\"app_exit_seen\":%s,\"app_exit_code\":%d,"
      "\"valid\":%s,\"quality\":\"%s\"",
      (unsigned long long)instructions, (unsigned long long)bytes,
      (unsigned long long)entries, (unsigned long long)movs_bytes,
      (unsigned long long)stos_bytes, (unsigned long long)movs_entries,
      (unsigned long long)stos_entries, (unsigned long long)unsupported,
      (unsigned long long)signals, (unsigned long long)started,
      (unsigned long long)ended, complete ? "true" : "false",
      error ? "true" : "false", exit_seen ? "true" : "false", exit_code,
      valid ? "true" : "false", signals ? "signals_observed" : "uninterrupted");
  if (latency_bound_enabled) {
    size_t used = strlen(fields);
    dr_snprintf(fields + used, sizeof(fields) - used, ",\"latency_bound_x16\":%llu",
                (unsigned long long)latency);
  }
  if (memory_operand_bytes_enabled) {
    size_t used = strlen(fields);
    dr_snprintf(fields + used, sizeof(fields) - used, ",\"memory_operand_bytes\":%llu",
                (unsigned long long)operand_bytes);
  }
  route_event(d, kind, fields);
}

/* The ordinary callback and the optional fused probe record the same replay
 * checkpoint. Its integer-only target matches the fused callback. */
#ifdef DURANTE_L1D_PROBE
__attribute__((target("general-regs-only")))
#endif
static inline void record_write_checkpoint(thread_data_t *data, app_pc pc,
                                           app_pc next_pc, int suffix) {
  data->write_pc = pc;
  data->write_next_pc = next_pc;
  data->write_suffix = suffix > 0 ? (uint64_t)suffix : 0;
  data->write_rep = false;
  /* The write belongs to the block most recently charged in this thread. */
  data->write_block_instructions = data->block_instructions;
  data->write_block_bound_x16 = data->block_bound_x16;
}

#ifdef DURANTE_L1D_PROBE
#include "../experiments/l1d_probe/probe.h"
static bool probe_partial_block;
#endif

static void set_raw_accounting_state(thread_data_t *d) {
  if (raw_tls_enabled) {
    byte *base = dr_get_dr_segment_base(raw_tls_register);
    *(thread_data_t **)(base + raw_tls_offset + RAW_TLS_STATE * sizeof(void *)) = d;
    d->raw_tls_base = base;
  }
}

static void sample_attach(thread_data_t *d) {
  if (!live_sampler)
    return;
  dr_mutex_lock(sample_lock);
  d->sample_prev = NULL;
  d->sample_next = sample_head;
  if (sample_head)
    ((thread_data_t *)sample_head)->sample_prev = d;
  sample_head = d;
  dr_mutex_unlock(sample_lock);
}

/* After this returns the sampler can no longer reach d, so it may be freed. */
static void sample_detach(thread_data_t *d) {
  if (!live_sampler)
    return;
  dr_mutex_lock(sample_lock);
  if (d->sample_prev)
    ((thread_data_t *)d->sample_prev)->sample_next = d->sample_next;
  else if (sample_head == d)
    sample_head = d->sample_next;
  if (d->sample_next)
    ((thread_data_t *)d->sample_next)->sample_prev = d->sample_prev;
  d->sample_next = d->sample_prev = NULL;
  dr_mutex_unlock(sample_lock);
}

/* entry_base counts only blocks before the latest entry, which replay can no
 * longer correct, so a sampled sum stays a lower bound. A thread with pending
 * restart or invalid work publishes nothing here; its own checkpoints decide. */
static void sample_threads(void *arg) {
  (void)arg;
  for (;;) {
    dr_sleep(1);
    dr_mutex_lock(sample_lock);
    for (thread_data_t *d = sample_head; d; d = d->sample_next) {
      if (invalid_thread_work(d) || __atomic_load_n(&d->restart_pc, __ATOMIC_RELAXED))
        continue;
      publish_instruction_target(&d->published_instructions,
                                 __atomic_load_n(&d->entry_base, __ATOMIC_RELAXED));
    }
    dr_mutex_unlock(sample_lock);
  }
}

static void thread_init(void *ctx) {
  thread_data_t *d = dr_thread_alloc(ctx, sizeof(*d));
  if (!d)
    fail("thread allocation failed");
  memset(d, 0, sizeof(*d));
#ifdef DURANTE_L1D_PROBE
  if (probe_l1d)
    probe_thread_init(d);
#endif
  d->child_file = -1;
  if (!drmgr_set_tls_field(ctx, tls_index, d))
    fail("TLS init failed");
  set_raw_accounting_state(d);
  sample_attach(d);
  process_id_t process = dr_get_process_id();
  if (process != owner_pid) {
    /* DR models CLONE_VM/vfork before exec as a separate drcontext sharing the
     * parent's client globals. Keep its counters, stream and exit state in TLS.
     */
    d->child_pid = process;
    dr_snprintf(d->child_segment, sizeof(d->child_segment), "%d-%llu",
                (int)process, (unsigned long long)dr_get_microseconds());
    start_record(d, "fork", owner_pid, segment);
    return;
  }
  dr_mutex_lock(state_lock);
  ++threads_started;
  dr_mutex_unlock(state_lock);
}
static void thread_exit(void *ctx) {
  thread_data_t *d = drmgr_get_tls_field(ctx, tls_index);
  /* Asynchronous thread termination can interrupt an arbitrary charged BB.
   * Only an explicit exit/exec checkpoint can attest to that final prefix. */
  if (live_work && d &&
      (d->instructions != __atomic_load_n(&d->published_instructions, __ATOMIC_ACQUIRE) ||
       d->rep_movs_bytes != d->published_movs || d->rep_stos_bytes != d->published_stos))
    invalidate_live_work();
  if (memory_operand_bytes_enabled && d && !d->child_pid &&
      d->pending_syscall != SYS_exit && d->pending_syscall != SYS_exit_group)
    d->latency_error = true; /* Another thread may have stopped this one mid-block. */
  if (d && d->child_pid) {
    /* This callback can run in the parent after child exec. Its fd number
     * refers to the child's descriptor table, so NEVER close that number in the
     * parent. Exec/_exit checkpoints close it in child context before the
     * transition. */
    sample_detach(d);
    drmgr_set_tls_field(ctx, tls_index, NULL);
    dr_thread_free(ctx, d, sizeof(*d));
    return;
  }
  dr_mutex_lock(state_lock);
  if (!add_thread(d)) {
    invalidate_live_work();
    accounting_error = true;
  }
  ++threads_ended;
  dr_mutex_unlock(state_lock);
  if (d) {
    sample_detach(d);
    drmgr_set_tls_field(ctx, tls_index, NULL);
    dr_thread_free(ctx, d, sizeof(*d));
  }
}

static dr_emit_flags_t analyze_block(void *drcontext, void *tag,
                                     instrlist_t *bb, bool for_trace,
                                     bool translating, void **user_data) {
  (void)for_trace;
  (void)translating;
  uint count = 0;
  bool in_emulation = false;
  for (instr_t *ins = instrlist_first(bb); ins != NULL;
       ins = instr_get_next(ins)) {
    if (drmgr_is_emulation_start(ins)) {
      ++count;
      in_emulation = true;
    } else if (drmgr_is_emulation_end(ins)) {
      in_emulation = false;
    } else if (!in_emulation && instr_is_app(ins)) {
      ++count;
    }
  }
  /* The low half carries the instruction count, the high half the optional
   * latency bound (UINT32_MAX when the block cannot be analyzed). */
  uint64_t bound = 0;
  if (latency_bound_enabled && count > 0)
    bound = durante_block_latency_bound(drcontext, tag, bb, count);
  *user_data = (void *)(ptr_uint_t)((uint64_t)count | bound << 32);
  return DR_EMIT_DEFAULT;
}

/* Version 1 sums fixed-size data operands from the decoded logical application
 * stream. Read-modify-write has both a source and destination; REP, predicated
 * and masked operations contribute nominal decoded widths once at block entry.
 * XSAVE's runtime-selected area has no fixed width. FXSAVE's 512 bytes do.
 * An address operand (LEA, NOP, prefetch, cache-line controls) carries no data. */
static uint32_t fixed_memory_bytes(opnd_t operand) {
  if (!opnd_is_memory_reference(operand))
    return 0;
  opnd_size_t size = opnd_get_size(operand);
  if (size == OPSZ_0 || size == OPSZ_NA || size == OPSZ_xsave)
    return 0;
  uint width = opnd_size_in_bytes(size);
  return width && opnd_size_from_bytes(width) == size ? width : 0;
}

static uint32_t instruction_memory_bytes(instr_t *ins) {
  int op = instr_get_opcode(ins);
  if (op == OP_lea || op == OP_nop_modrm || instr_is_prefetch(ins) ||
      op == OP_clflush || op == OP_clflushopt || op == OP_clwb ||
      op == OP_cldemote || op == OP_invlpg || op == OP_bndmk ||
      op == OP_bndcl || op == OP_bndcu || op == OP_bndcn)
    return 0;
  uint32_t bytes = 0;
  for (int i = 0; i < instr_num_srcs(ins); ++i)
    bytes += fixed_memory_bytes(instr_get_src(ins, i));
  for (int i = 0; i < instr_num_dsts(ins); ++i)
    bytes += fixed_memory_bytes(instr_get_dst(ins, i));
  return bytes;
}

static uint32_t suffix_memory_bytes(instr_t *start) {
  uint64_t bytes = 0;
  bool in_emulation = false;
  for (instr_t *ins = start; ins; ins = instr_get_next(ins)) {
    if (drmgr_is_emulation_start(ins)) {
      emulated_instr_t logical = {.size = sizeof(logical)};
      if (!drmgr_get_emulated_instr_data(ins, &logical) || !logical.instr)
        return UINT32_MAX;
      bytes += instruction_memory_bytes(logical.instr);
      in_emulation = true;
    } else if (drmgr_is_emulation_end(ins))
      in_emulation = false;
    else if (!in_emulation && instr_is_app(ins))
      bytes += instruction_memory_bytes(ins);
  }
  return bytes < UINT32_MAX ? (uint32_t)bytes : UINT32_MAX;
}

/* Optional byte demand is deliberately separate from instruction count.  A
 * clean call only at REP MOVS/STOS observes the architectural repeat register
 * at that instruction, after any application instructions earlier in the BB.
 * Signals mark quality explicitly; requested bytes are not completed memory
 * transfers. CMPS/SCAS can stop early and
 * are counted as unsupported entries instead of charging their initial RCX.
 */
static void observe_rep(app_pc pc, int opcode, int bytes, int count_reg) {
  void *drcontext = dr_get_current_drcontext();
  thread_data_t *data = drmgr_get_tls_field(drcontext, tls_index);
  if (data == NULL)
    fail("REP observation without thread state");
  settle_restart(data);
  data->fast_until = 0;
  uint64_t repeats = 0;
  if (bytes != 0) {
    dr_mcontext_t mc = {0};
    mc.size = sizeof(mc);
    mc.flags = DR_MC_INTEGER;
    if (count_reg == DR_REG_NULL || !dr_get_mcontext(drcontext, &mc)) {
      counter_error(&data->rep_error);
      return;
    }
    repeats = reg_get_value((reg_id_t)count_reg, &mc);
  }
  bool resumed = data->restart_pc != NULL;
  uint64_t request = repeats;
  if (resumed) {
    if (!data->restart_entered || !data->restart_rep ||
        data->restart_pc != pc || data->restart_rep_opcode != opcode ||
        data->restart_rep_bytes != bytes ||
        data->restart_rep_count_reg != count_reg ||
        repeats > data->restart_rep_request) {
      counter_error(&data->replay_error);
      data->restart_pc = NULL;
      return;
    }
    /* A protected later page can fault after earlier REP iterations complete.
     * Keep the original requested work, not the reduced architectural RCX. */
    request = data->restart_rep_request;
    data->restart_pc = NULL;
  }
  if (data->write_pc == pc) {
    data->write_rep = true;
    data->write_rep_opcode = opcode;
    data->write_rep_bytes = bytes;
    data->write_rep_count_reg = count_reg;
    data->write_rep_request = request;
  }
  if (resumed)
    return;
  if (bytes == 0) {
    invalidate_live_work();
    if (data->unsupported_rep_entries == UINT64_MAX)
      counter_error(&data->rep_error);
    else
      ++data->unsupported_rep_entries;
    return;
  }
  if ((opcode != OP_rep_movs && opcode != OP_rep_stos) ||
      (bytes != 1 && bytes != 2 && bytes != 4 && bytes != 8) ||
      repeats > UINT64_MAX / (unsigned)bytes) {
    counter_error(&data->rep_error);
    return;
  }
  uint64_t requested_bytes = repeats * (unsigned)bytes;
  uint64_t *kind_bytes =
      opcode == OP_rep_movs ? &data->rep_movs_bytes : &data->rep_stos_bytes;
  uint64_t *kind_entries =
      opcode == OP_rep_movs ? &data->rep_movs_entries : &data->rep_stos_entries;
  /* Commit the aggregate and operation-specific counters together. Even a
   * zero-repeat observation contributes one entry to its original opcode. */
  if (UINT64_MAX - data->rep_bytes < requested_bytes ||
      data->rep_entries == UINT64_MAX ||
      UINT64_MAX - *kind_bytes < requested_bytes ||
      *kind_entries == UINT64_MAX) {
    counter_error(&data->rep_error);
    return;
  }
  data->rep_bytes += requested_bytes;
  ++data->rep_entries;
  *kind_bytes += requested_bytes;
  ++*kind_entries;
}

/* A shared-VM child must take DR's ordinary syscall-exit path so its suspended
 * parent keeps the correct runtime state. Only our exit syscall may run after
 * this decision, never application handlers or the rejected instruction. */
static void cpu_policy_exit(void) {
  invalidate_live_work();
  void *ctx = dr_get_current_drcontext();
  thread_data_t *d = drmgr_get_tls_field(ctx, tls_index);
  if (!d)
    fail("CPU policy exit without valid thread state");
  if (d->child_pid) {
    dr_mcontext_t mc = {.size = sizeof(mc), .flags = DR_MC_ALL};
    d->child_policy_exit = true;
    if (!dr_get_mcontext(ctx, &mc))
      fail("cannot redirect CPU policy exit");
    mc.xax = SYS_exit_group;
    mc.xdi = 126;
    mc.pc = cpu_policy_exit_pc;
    dr_redirect_execution(&mc);
    fail("cannot execute CPU policy exit");
  }
  dr_abort_with_code(126);
}

/* The client implements the Linux x86-64 syscall ABI only. In particular an
 * int 0x80 fork can create a child without either the native spawn callback or
 * the child's client running. Measurement-only mode preserves the instruction
 * but invalidates the partial tree. CPU policy mode stops before execution. */
static void unsupported_syscall_abi(void) {
  __atomic_store_n(&static_text_revoked, true, __ATOMIC_RELEASE);
  invalidate_live_work();
  if (deny_avx || deny_simd) {
    dr_fprintf(STDERR, "durante: syscall ABI unsupported with CPU policy\n");
    cpu_policy_exit();
  }
  dr_mutex_lock(state_lock);
  accounting_error = true;
  dr_mutex_unlock(state_lock);
}

static bool x32_syscall(int number) {
  return number >= 0 && (((unsigned int)number & X32_SYSCALL_BIT) != 0);
}

static inline thread_data_t *accounting_thread_data(void) {
#ifdef DURANTE_L1D_PROBE
  if (probe_l1d)
    return probe_thread_data;
#endif
  return drmgr_get_tls_field(dr_get_current_drcontext(), tls_index);
}

static thread_data_t *charge_block(int instructions, app_pc first_pc) {
  thread_data_t *data = accounting_thread_data();
  if (data == NULL || instructions <= 0)
    fail("instruction count without valid thread state");
  if (data->restart_pc) {
    if (data->restart_entered || data->restart_pc != first_pc) {
      counter_error(&data->replay_error);
      data->restart_pc = NULL;
    } else if (data->restart_rep) {
      data->restart_entered = true;
    } else {
      data->restart_pc = NULL;
    }
  }
  data->write_pc = NULL;
  if (live_work)
    publish_live_work(data, false);
  if (UINT64_MAX - data->instructions < (uint64_t)instructions) {
    /* Sticky invalidity: subsequent blocks cannot make an overflow usable. */
    data->counter_overflows = 1;
    invalidate_live_work();
    return NULL;
  }
  data->instructions += (uint64_t)instructions;
#ifdef DURANTE_TEST_COUNT_LOCATIONS
  profile_count(first_pc, (uint64_t)instructions);
#endif
  return data;
}

static void count_block(int instructions, app_pc first_pc) {
  charge_block(instructions, first_pc);
}

/* Up to the next publication, an ordinary BB only clears the write checkpoint
 * and charges its count. REP can introduce a publishable byte delta, and replay
 * can roll back instructions or require a restart. Both callbacks disarm the
 * window. A syscall flush publishes early and can only move the boundary later. */
static void charge_boundary(int count, app_pc first_pc) {
  thread_data_t *d = charge_block(count, first_pc);
  if (!d)
    return;
  d->fast_until = 0;
  if (d->restart_pc || d->instructions > UINT64_MAX - INT32_MAX - LIVE_WORK_BATCH)
    return;
  /* A permanently invalid thread can no longer publish. Other threads sharing
   * its page still publish their own deltas. Reserve room for the largest BB. */
  if (!live_work || invalid_thread_work(d))
    d->fast_until = UINT64_MAX - INT32_MAX;
  else if (d->published_instructions <= d->instructions)
    d->fast_until = d->published_instructions + LIVE_WORK_BATCH;
}

static opnd_t raw_slot(uint slot) {
  return opnd_create_far_base_disp(raw_tls_register, DR_REG_NULL, DR_REG_NULL, 0,
                                   (int)(raw_tls_offset + slot * sizeof(void *)), OPSZ_PTR);
}

static opnd_t state_field(size_t offset) {
  return opnd_create_base_disp(DR_REG_R11, DR_REG_NULL, 0, (int)offset, OPSZ_8);
}

/* MOV, MOVZX, LEA and these branches preserve all arithmetic flags. The
 * scratch registers are restored before the first application instruction.
 * Both guard paths precede the charge store and the final RCX restore, so
 * restore_state() can recognize partial entries by those two instructions. */
static void emit_flat_charge(void *ctx, instrlist_t *bb, instr_t *where, int count) {
  opnd_t value = opnd_create_reg(DR_REG_RCX), state = opnd_create_reg(DR_REG_R11);
  opnd_t low_reg = opnd_create_reg(DR_REG_ECX);
  instr_t *wrapped = INSTR_CREATE_label(ctx), *checked = INSTR_CREATE_label(ctx);
#define INSERT(i) \
  (where ? instrlist_meta_preinsert(bb, where, (i)) : instrlist_meta_append(bb, (i)))
  INSERT(INSTR_CREATE_mov_st(ctx, raw_slot(RAW_TLS_SPILL_RCX), value));
  INSERT(INSTR_CREATE_mov_st(ctx, raw_slot(RAW_TLS_SPILL_R11), state));
  INSERT(INSTR_CREATE_mov_ld(ctx, state, raw_slot(RAW_TLS_STATE)));
  /* A block adds at most INT_MAX. On x86-64 its most significant byte
   * can increase by at most one, and goes from nonzero to zero only on wrap.
   * Check the completed entry before replacing its base. This keeps the new
   * charge reversible if restore_state() relocates this thread mid-entry. */
  INSERT(INSTR_CREATE_movzx(ctx, low_reg,
                            OPND_CREATE_MEM8(DR_REG_R11, offsetof(thread_data_t, entry_base) + 7)));
  INSERT(INSTR_CREATE_jecxz(ctx, opnd_create_instr(checked)));
  INSERT(INSTR_CREATE_movzx(ctx, low_reg,
                            OPND_CREATE_MEM8(DR_REG_R11, offsetof(thread_data_t, instructions) + 7)));
  INSERT(INSTR_CREATE_jecxz(ctx, opnd_create_instr(wrapped)));
  INSERT(INSTR_CREATE_jmp_short(ctx, opnd_create_instr(checked)));
  INSERT(wrapped);
  INSERT(INSTR_CREATE_mov_st(ctx, state_field(offsetof(thread_data_t, counter_overflows)),
                             OPND_CREATE_INT32(1)));
  INSERT(checked);
  INSERT(INSTR_CREATE_mov_ld(ctx, value, state_field(offsetof(thread_data_t, instructions))));
  INSERT(INSTR_CREATE_mov_st(ctx, state_field(offsetof(thread_data_t, entry_base)), value));
  INSERT(INSTR_CREATE_lea(ctx, value,
                          opnd_create_base_disp(DR_REG_RCX, DR_REG_NULL, 0, count, OPSZ_lea)));
  INSERT(INSTR_CREATE_mov_st(ctx, state_field(offsetof(thread_data_t, instructions)), value));
  INSERT(INSTR_CREATE_mov_st(ctx, state_field(offsetof(thread_data_t, write_pc)),
                             OPND_CREATE_INT32(0)));
  INSERT(INSTR_CREATE_mov_ld(ctx, state, raw_slot(RAW_TLS_SPILL_R11)));
  INSERT(INSTR_CREATE_mov_ld(ctx, value, raw_slot(RAW_TLS_SPILL_RCX)));
#undef INSERT
}

/* The runtime re-encodes every meta instruction of each new block, which
 * dominates block building for interpreters and generated code. These
 * sequences depend only on the block size or on a write's immediates, so they
 * are encoded once and inserted as one raw copy. A valid opcode and an empty
 * operand list keep the runtime from decoding the bundle; restore_state()
 * decodes the code cache itself. */
enum { FLAT_ENTRY_CACHE = 1024, SEQUENCE_BYTES = 128 };
typedef struct {
  uint length;
  byte bytes[SEQUENCE_BYTES];
} encoded_sequence_t;
static encoded_sequence_t *flat_entries[FLAT_ENTRY_CACHE + 1];

static uint encode_sequence(void *ctx, instrlist_t *ilist, byte *buf) {
  byte *end = instrlist_encode(ctx, ilist, buf, true);
  if (!end || end - buf > SEQUENCE_BYTES)
    fail("cannot pre-encode instrumentation");
  return (uint)(end - buf);
}

static void insert_bundle(void *ctx, instrlist_t *bb, instr_t *where, const byte *bytes,
                          uint length) {
  instr_t *bundle = instr_build_bits(ctx, OP_mov_st, length);
  instr_set_raw_bytes(bundle, (byte *)bytes, length);
  /* No operands: mangling's RIP-relative scan must not decode the bundle,
   * which would cut its raw bits down to the first instruction. */
  instr_set_operands_valid(bundle, true);
  instrlist_meta_preinsert(bb, where, bundle);
}

static void insert_flat_charge(void *ctx, instrlist_t *bb, instr_t *where, int count) {
  if (count <= 0 || count > FLAT_ENTRY_CACHE) {
    emit_flat_charge(ctx, bb, where, count);
    return;
  }
  encoded_sequence_t *entry = __atomic_load_n(&flat_entries[count], __ATOMIC_ACQUIRE);
  if (!entry) {
    encoded_sequence_t *fresh = dr_global_alloc(sizeof(*fresh));
    instrlist_t *ilist = instrlist_create(ctx);
    emit_flat_charge(ctx, ilist, NULL, count);
    fresh->length = encode_sequence(ctx, ilist, fresh->bytes);
    instrlist_clear_and_destroy(ctx, ilist);
    if (__atomic_compare_exchange_n(&flat_entries[count], &entry, fresh, false,
                                    __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE))
      entry = fresh;
    else
      dr_global_free(fresh, sizeof(*fresh));
  }
  insert_bundle(ctx, bb, where, entry->bytes, entry->length);
}

static bool is_raw_slot(opnd_t opnd, uint slot) {
  int offset = (int)(raw_tls_offset + slot * sizeof(void *));
  if (!opnd_is_far_memory_reference(opnd) || opnd_get_segment(opnd) != raw_tls_register)
    return false;
  if (opnd_is_far_base_disp(opnd))
    return opnd_get_base(opnd) == DR_REG_NULL && opnd_get_index(opnd) == DR_REG_NULL &&
           opnd_get_disp(opnd) == offset;
  return opnd_is_abs_addr(opnd) && opnd_get_addr(opnd) == (void *)(ptr_int_t)offset;
}

static bool is_reg(opnd_t opnd, reg_id_t reg) {
  return opnd_is_reg(opnd) && opnd_get_reg(opnd) == reg;
}

/* The runtime can translate a thread stopped inside this client's inline
 * sequences (synchronization, relocation). Recover rcx/r11 from the private
 * spill slots. If a relocated thread will re-enter a flat block whose charge
 * has already been stored, return the counter to its pre-entry value: the
 * re-entry charges the block again. Restoration is idempotent. */
static bool restore_state(void *drcontext, bool restore_memory, dr_restore_state_info_t *info) {
  if (!raw_tls_enabled || !info || !info->raw_mcontext || !info->mcontext ||
      !info->fragment_info.cache_start_pc)
    return true;
  thread_data_t *d = drmgr_get_tls_field(drcontext, tls_index);
  if (!d || !d->raw_tls_base)
    return true;
  byte *pc = info->fragment_info.cache_start_pc, *stop = info->raw_mcontext->pc;
  bool spilled_rcx = false, spilled_r11 = false, charged = false;
  instr_t ins;
  instr_init(drcontext, &ins);
  for (uint n = 0; pc != NULL && pc < stop && n < 4096; ++n) {
    instr_reset(drcontext, &ins);
    byte *next = decode(drcontext, pc, &ins);
    if (next == NULL)
      break;
    int op = instr_get_opcode(&ins);
    if (op == OP_mov_st && instr_num_dsts(&ins) == 1 && instr_num_srcs(&ins) == 1) {
      opnd_t dst = instr_get_dst(&ins, 0), src = instr_get_src(&ins, 0);
      if (is_raw_slot(dst, RAW_TLS_SPILL_RCX) && is_reg(src, DR_REG_RCX))
        spilled_rcx = true;
      else if (is_raw_slot(dst, RAW_TLS_SPILL_R11) && is_reg(src, DR_REG_R11))
        spilled_r11 = true;
      else if (flat_inline && spilled_rcx && spilled_r11 && is_reg(src, DR_REG_RCX) &&
               opnd_is_base_disp(dst) && opnd_get_base(dst) == DR_REG_R11 &&
               opnd_get_index(dst) == DR_REG_NULL &&
               opnd_get_disp(dst) == (int)offsetof(thread_data_t, instructions))
        charged = true;
    } else if (op == OP_mov_ld && instr_num_dsts(&ins) == 1 && instr_num_srcs(&ins) == 1) {
      opnd_t dst = instr_get_dst(&ins, 0), src = instr_get_src(&ins, 0);
      if (is_raw_slot(src, RAW_TLS_SPILL_RCX) && is_reg(dst, DR_REG_RCX)) {
        spilled_rcx = false;
        charged = false; /* The entry sequence is complete. */
      } else if (is_raw_slot(src, RAW_TLS_SPILL_R11) && is_reg(dst, DR_REG_R11)) {
        spilled_r11 = false;
      }
    }
    pc = next;
  }
  instr_free(drcontext, &ins);
  reg_t *slots = (reg_t *)(d->raw_tls_base + raw_tls_offset);
  if (spilled_rcx)
    info->mcontext->xcx = slots[RAW_TLS_SPILL_RCX];
  if (spilled_r11)
    info->mcontext->r11 = slots[RAW_TLS_SPILL_R11];
  if (charged && restore_memory)
    d->instructions = d->entry_base;
  return true;
}

static void restore_charge_state(void *ctx, instrlist_t *bb, instr_t *where) {
  dr_restore_reg(ctx, bb, where, DR_REG_R11, SPILL_SLOT_2);
  dr_restore_reg(ctx, bb, where, DR_REG_R10, SPILL_SLOT_1);
  dr_restore_arith_flags(ctx, bb, where, SPILL_SLOT_3);
}

static void insert_read_accounting_state(void *ctx, instrlist_t *bb, instr_t *where,
                                         reg_id_t reg) {
  if (raw_tls_enabled)
    instrlist_meta_preinsert(bb, where,
                             INSTR_CREATE_mov_ld(ctx, opnd_create_reg(reg),
                                                 raw_slot(RAW_TLS_STATE)));
  else if (!drmgr_insert_read_tls_field(ctx, tls_index, bb, where, reg))
    fail("cannot load accounting TLS");
}

static void insert_charge(void *ctx, instrlist_t *bb, instr_t *where, int count,
                          app_pc first_pc) {
  instr_t *slow = INSTR_CREATE_label(ctx), *done = INSTR_CREATE_label(ctx);
  opnd_t tmp = opnd_create_reg(DR_REG_R10);
  opnd_t boundary = opnd_create_base_disp(DR_REG_R11, DR_REG_NULL, 0,
                                          offsetof(thread_data_t, fast_until), OPSZ_8);
  opnd_t instructions = opnd_create_base_disp(DR_REG_R11, DR_REG_NULL, 0,
                                              offsetof(thread_data_t, instructions), OPSZ_8);
  opnd_t checkpoint = opnd_create_base_disp(DR_REG_R11, DR_REG_NULL, 0,
                                            offsetof(thread_data_t, write_pc), OPSZ_8);
  /* The spills and both branches are confined to this instruction's meta IR.
   * Preserve the application's six arithmetic flags, including on the slow path. */
  dr_save_arith_flags(ctx, bb, where, SPILL_SLOT_3);
  dr_save_reg(ctx, bb, where, DR_REG_R10, SPILL_SLOT_1);
  dr_save_reg(ctx, bb, where, DR_REG_R11, SPILL_SLOT_2);
#define INSERT(i) instrlist_meta_preinsert(bb, where, (i))
  insert_read_accounting_state(ctx, bb, where, DR_REG_R11);
  INSERT(INSTR_CREATE_mov_ld(ctx, tmp, instructions));
  INSERT(INSTR_CREATE_cmp(ctx, tmp, boundary));
  INSERT(INSTR_CREATE_jcc(ctx, OP_jae, opnd_create_instr(slow)));
  INSERT(INSTR_CREATE_add(ctx, tmp, OPND_CREATE_INT32(count)));
  INSERT(INSTR_CREATE_mov_st(ctx, instructions, tmp));
  INSERT(INSTR_CREATE_mov_st(ctx, checkpoint, OPND_CREATE_INT32(0)));
  restore_charge_state(ctx, bb, where);
  INSERT(INSTR_CREATE_jmp(ctx, opnd_create_instr(done)));
  INSERT(slow);
  restore_charge_state(ctx, bb, where);
  dr_insert_clean_call(ctx, bb, where, (void *)charge_boundary, false, 2,
                       OPND_CREATE_INT32(count), OPND_CREATE_INTPTR(first_pc));
  INSERT(done);
#undef INSERT
}

/* The bound is charged at the same point as the count, so a block's bound is
 * counted exactly when its instructions are. */
__attribute__((target("general-regs-only")))
static thread_data_t *count_block_latency(int instructions, app_pc first_pc, int bound_x16) {
  thread_data_t *data = charge_block(instructions, first_pc);
  if (data == NULL)
    return NULL;
  data->block_instructions = (uint32_t)instructions;
  data->block_bound_x16 = (uint32_t)bound_x16;
  if (bound_x16 < 0)
    counter_error(&data->latency_error);
  else if (UINT64_MAX - data->latency_bound_x16 < (uint64_t)bound_x16)
    data->counter_overflows = 1;
  else
    data->latency_bound_x16 += (uint64_t)bound_x16;
  return data;
}

/* Keep the incremental charge scalar even if the compiler coalesces adjacent
 * counters. The standard clean call also preserves application XMM/YMM/ZMM,
 * including across helpers. save_fpstate is only needed for x87/MMX. */
__attribute__((target("general-regs-only")))
static void count_block_memory(int instructions, app_pc pc, int bound_x16, uint32_t bytes) {
  thread_data_t *d = count_block_latency(instructions, pc, bound_x16);
  if (d != NULL) {
    if (bytes == UINT32_MAX || UINT64_MAX - d->memory_operand_bytes < bytes)
      counter_error(&d->latency_error);
    else
      d->memory_operand_bytes += bytes;
  }
}

/* A fault can leave the middle of a BB whose entire length was charged at
 * entry. Save its unexecuted suffix at the write, not a whole-counter snapshot:
 * already executed prefixes and intervening signal work must remain charged.
 */
__attribute__((target("general-regs-only")))
static thread_data_t *before_write(app_pc pc, app_pc next_pc, int suffix) {
  thread_data_t *data = accounting_thread_data();
  if (data == NULL)
    fail("write checkpoint without thread state");
  record_write_checkpoint(data, pc, next_pc, suffix);
  return data;
}

__attribute__((target("general-regs-only")))
static void before_write_memory(app_pc pc, app_pc next_pc, int suffix,
                                uint32_t suffix_bytes, uint32_t operand_bytes) {
  thread_data_t *d = before_write(pc, next_pc, suffix);
  d->write_suffix_bytes = suffix_bytes;
  d->write_operand_bytes = operand_bytes;
}

/* The ordinary clock has no latency share at replay. Save its checkpoint
 * directly with flag-neutral moves. The scratch register is always restored
 * before the application instruction, even when normal liveness treats it
 * as dead: a synchronous signal handler may read every saved register. */
/* Raw-TLS write checkpoint: a fixed layout whose five 32-bit immediates are
 * patched per write. Both pointers always use two 32-bit stores. */
enum { CHECKPOINT_PC_LO, CHECKPOINT_PC_HI, CHECKPOINT_NEXT_LO, CHECKPOINT_NEXT_HI,
       CHECKPOINT_SUFFIX, CHECKPOINT_IMMEDIATES };
typedef struct {
  encoded_sequence_t code;
  uint immediate[CHECKPOINT_IMMEDIATES];
} checkpoint_template_t;
static checkpoint_template_t *checkpoint_template;

static checkpoint_template_t *write_checkpoint_template(void *ctx) {
  checkpoint_template_t *t = __atomic_load_n(&checkpoint_template, __ATOMIC_ACQUIRE);
  if (t)
    return t;
  checkpoint_template_t *fresh = dr_global_alloc(sizeof(*fresh));
  const reg_id_t data = DR_REG_R11;
  instrlist_t *ilist = instrlist_create(ctx);
  instr_t *imm[CHECKPOINT_IMMEDIATES];
  instrlist_meta_append(ilist, INSTR_CREATE_mov_st(ctx, raw_slot(RAW_TLS_SPILL_R11),
                                                    opnd_create_reg(data)));
  instrlist_meta_append(ilist, INSTR_CREATE_mov_ld(ctx, opnd_create_reg(data),
                                                    raw_slot(RAW_TLS_STATE)));
  static const size_t fields[CHECKPOINT_IMMEDIATES] = {
      offsetof(thread_data_t, write_pc), offsetof(thread_data_t, write_pc) + 4,
      offsetof(thread_data_t, write_next_pc), offsetof(thread_data_t, write_next_pc) + 4,
      offsetof(thread_data_t, write_suffix)};
  for (int i = 0; i < CHECKPOINT_IMMEDIATES; ++i) {
    opnd_t dst = i == CHECKPOINT_SUFFIX ? OPND_CREATE_MEM64(data, (int)fields[i])
                                        : OPND_CREATE_MEM32(data, (int)fields[i]);
    imm[i] = INSTR_CREATE_mov_st(ctx, dst, OPND_CREATE_INT32(0x7fffffff));
    instrlist_meta_append(ilist, imm[i]);
  }
  instrlist_meta_append(ilist, INSTR_CREATE_mov_st(ctx, OPND_CREATE_MEM8(data,
                                                    offsetof(thread_data_t, write_rep)),
                                                    OPND_CREATE_INT8(0)));
  instrlist_meta_append(ilist, INSTR_CREATE_mov_ld(ctx, opnd_create_reg(data),
                                                    raw_slot(RAW_TLS_SPILL_R11)));
  fresh->code.length = encode_sequence(ctx, ilist, fresh->code.bytes);
  uint offset = 0;
  int index = 0;
  for (instr_t *in = instrlist_first(ilist); in; in = instr_get_next(in)) {
    offset += instr_length(ctx, in);
    if (index < CHECKPOINT_IMMEDIATES && in == imm[index])
      fresh->immediate[index++] = offset - 4; /* imm32 is each store's last field */
  }
  instrlist_clear_and_destroy(ctx, ilist);
  if (index != CHECKPOINT_IMMEDIATES || offset != fresh->code.length)
    fail("cannot lay out the write checkpoint");
  if (__atomic_compare_exchange_n(&checkpoint_template, &t, fresh, false, __ATOMIC_ACQ_REL,
                                  __ATOMIC_ACQUIRE))
    return fresh;
  dr_global_free(fresh, sizeof(*fresh));
  return t;
}

static void insert_write_checkpoint(void *ctx, instrlist_t *bb, instr_t *where,
                                    app_pc pc, app_pc next_pc, int suffix) {
  if (raw_tls_enabled) {
    const checkpoint_template_t *t = write_checkpoint_template(ctx);
    byte code[SEQUENCE_BYTES];
    memcpy(code, t->code.bytes, t->code.length);
    const uint32_t value[CHECKPOINT_IMMEDIATES] = {
        (uint32_t)(ptr_uint_t)pc, (uint32_t)((ptr_uint_t)pc >> 32),
        (uint32_t)(ptr_uint_t)next_pc, (uint32_t)((ptr_uint_t)next_pc >> 32),
        (uint32_t)suffix};
    for (int i = 0; i < CHECKPOINT_IMMEDIATES; ++i)
      memcpy(code + t->immediate[i], &value[i], sizeof(value[i]));
    insert_bundle(ctx, bb, where, code, t->code.length);
    return;
  }
  const reg_id_t data = DR_REG_R11;
  if (raw_tls_enabled)
    instrlist_meta_preinsert(bb, where, INSTR_CREATE_mov_st(ctx, raw_slot(RAW_TLS_SPILL_R11),
                                                            opnd_create_reg(data)));
  else
    dr_save_reg(ctx, bb, where, data, SPILL_SLOT_1);
  insert_read_accounting_state(ctx, bb, where, data);
  opnd_t address = opnd_create_base_disp(data, DR_REG_NULL, 0,
                                         offsetof(thread_data_t, write_pc), OPSZ_PTR);
  instrlist_insert_mov_immed_ptrsz(ctx, (ptr_int_t)pc, address, bb, where, NULL, NULL);
  opnd_set_disp(&address, offsetof(thread_data_t, write_next_pc));
  instrlist_insert_mov_immed_ptrsz(ctx, (ptr_int_t)next_pc, address, bb, where, NULL, NULL);
  opnd_set_disp(&address, offsetof(thread_data_t, write_suffix));
  instrlist_meta_preinsert(bb, where,
                          INSTR_CREATE_mov_st(ctx, address, OPND_CREATE_INT32(suffix)));
  opnd_set_disp(&address, offsetof(thread_data_t, write_rep));
  opnd_set_size(&address, OPSZ_1);
  instrlist_meta_preinsert(bb, where, INSTR_CREATE_mov_st(ctx, address, OPND_CREATE_INT8(0)));
  if (raw_tls_enabled)
    instrlist_meta_preinsert(bb, where, INSTR_CREATE_mov_ld(ctx, opnd_create_reg(data),
                                                            raw_slot(RAW_TLS_SPILL_R11)));
  else
    dr_restore_reg(ctx, bb, where, data, SPILL_SLOT_1);
}

static void code_replay(void *ctx, int reason, app_pc source_pc,
                        app_pc target_pc) {
#ifdef DURANTE_L1D_PROBE
  if (probe_l1d && reason != DR_DURANTE_REPLAY_ENTRY)
    probe_partial_block = true;
#endif
  thread_data_t *d = drmgr_get_tls_field(ctx, tls_index);
  if (!d) {
    invalidate_live_work();
    __atomic_store_n(&replay_without_tls, 1, __ATOMIC_RELAXED);
    return;
  }
  check_flat_counter(d);
  settle_restart(d);
  d->fast_until = 0;
  if (reason == DR_DURANTE_REPLAY_ENTRY) {
    /* Cache validation ran before client entry instrumentation. Nothing was
     * charged; a pending retry still belongs to the same logical instruction. */
    if (d->restart_pc && d->restart_pc != target_pc) {
      counter_error(&d->replay_error);
      d->restart_pc = NULL;
    }
    d->write_pc = NULL;
    return;
  }
  bool retry = reason == DR_DURANTE_REPLAY_RETRY;
  bool completed = reason == DR_DURANTE_REPLAY_AFTER_WRITE;
  if ((!retry && !completed) || !d->write_pc || !d->write_suffix ||
      d->restart_pc || d->counter_overflows ||
      (retry && (source_pc != d->write_pc || target_pc != source_pc)) ||
      (completed && target_pc != d->write_next_pc)) {
    counter_error(&d->replay_error);
    d->restart_pc = d->write_pc = NULL;
    return;
  }
  uint64_t rollback = d->write_suffix - (completed ? 1 : 0);
  if (d->instructions < rollback) {
    counter_error(&d->replay_error);
    d->write_pc = NULL;
    return;
  }
  if (latency_bound_enabled) {
    /* The unexecuted suffix returns its proportional share of the block's
     * bound, rounded down, so every block keeps at least issue cost. */
    uint64_t share = 0;
    if (d->write_block_instructions == 0 || rollback > d->write_block_instructions)
      counter_error(&d->latency_error);
    else
      share = (uint64_t)d->write_block_bound_x16 * rollback / d->write_block_instructions;
    if (d->latency_bound_x16 < share)
      counter_error(&d->latency_error);
    else
      d->latency_bound_x16 -= share;
  }
  if (memory_operand_bytes_enabled) {
    uint32_t suffix = d->write_suffix_bytes;
    uint32_t retain = completed ? d->write_operand_bytes : 0;
    if (suffix == UINT32_MAX || retain > suffix || d->memory_operand_bytes < suffix - retain)
      counter_error(&d->latency_error);
    else
      d->memory_operand_bytes -= suffix - retain;
  }
  d->instructions -= rollback;
#ifdef DURANTE_TEST_COUNT_LOCATIONS
  profile_replay(rollback);
#endif
  if (retry) {
    d->restart_pc = target_pc;
    d->restart_base = d->instructions;
    d->restart_entered = false;
    d->restart_rep = d->write_rep;
    d->restart_rep_opcode = d->write_rep_opcode;
    d->restart_rep_bytes = d->write_rep_bytes;
    d->restart_rep_count_reg = d->write_rep_count_reg;
    d->restart_rep_request = d->write_rep_request;
  }
  d->write_pc = NULL;
}

static void kernel_xfer(void *ctx, const dr_kernel_xfer_info_t *info) {
  (void)info;
  thread_data_t *d = drmgr_get_tls_field(ctx, tls_index);
  if (!d)
    return;
  settle_restart(d);
  /* Signal handlers can call the same code at the same PC with another BB
   * suffix. PC equality cannot authenticate an interrupted TLS checkpoint.
   * An unrelated earlier signal does not make later JIT work invalid. */
  d->write_pc = NULL;
  if (d->restart_pc) {
    counter_error(&d->replay_error);
    d->restart_pc = NULL;
  }
}

static uint write_suffix(instrlist_t *bb, instr_t *where) {
  uint count = 0;
  bool reached = false, in_emulation = false;
  for (instr_t *ins = instrlist_first(bb); ins; ins = instr_get_next(ins)) {
    if (ins == where && in_emulation)
      return 0; /* No exact logical suffix for a physical emulation write. */
    reached = reached || ins == where;
    if (drmgr_is_emulation_start(ins)) {
      if (reached)
        ++count;
      in_emulation = true;
    } else if (drmgr_is_emulation_end(ins)) {
      in_emulation = false;
    } else if (reached && !in_emulation && instr_is_app(ins)) {
      ++count;
    }
  }
  return count;
}

static void cpu_feature_violation(int feature) {
  if (feature == CPU_POLICY_UNDECODED) {
    dr_fprintf(STDERR, "durante: instruction unsupported by native CPU policy decoder\n");
    cpu_policy_exit();
  }
  void *ctx = dr_get_current_drcontext();
  thread_data_t *d = drmgr_get_tls_field(ctx, tls_index);
  if (!d || (feature != CPU_POLICY_AVX && feature != CPU_POLICY_SIMD))
    fail("CPU policy observation without valid thread state");
  /* route_event keeps a shared-VM/vfork child's report out of its parent's
   * stream. No parent counter or terminal state is modified here. */
  dr_mutex_lock(state_lock);
  route_event(d, "cpu_feature_violation", feature == CPU_POLICY_AVX
              ? ",\"feature\":\"avx\"" : ",\"feature\":\"simd\"");
  dr_mutex_unlock(state_lock);
  /* This bounded event invalidates the whole measurement before termination. */
  cpu_policy_exit();
}

/* The profile has no defined value for this request. Charge the instruction
 * as usual but make the whole measurement unusable. */
static void cpuid_request_invalid(void *ctx) {
  thread_data_t *d = drmgr_get_tls_field(ctx, tls_index);
  if (!d)
    fail("CPUID profile observation without valid thread state");
  counter_error(&d->cpuid_error);
}

#ifndef DURANTE_TEST_COUNT_LOCATIONS
/* A block that branches to its own entry can amortize inline instrumentation
 * immediately. Other startup blocks use compact callbacks. Keep the address
 * translations for blocks inlined based on the live count: on a later
 * translating=true callback DR is recreating a block with a static layout. */
static bool select_inline_block(void *ctx, instrlist_t *bb, bool translating,
                                dr_emit_flags_t *flags) {
  instr_t *tail = instrlist_last_app(bb);
  bool self_loop = false;
  if (tail && (instr_is_cbr(tail) || instr_is_ubr(tail))) {
    opnd_t target = instr_get_target(tail);
    self_loop = opnd_is_pc(target) &&
                opnd_get_pc(target) == instr_get_app_pc(instrlist_first_app(bb));
  }
  if (self_loop)
    return true;
  if (translating)
    return false;
  thread_data_t *d = drmgr_get_tls_field(ctx, tls_index);
  if (!d)
    return false;
  /* Static programs commonly enter their main loop after parsing input. Keep
   * the longer startup period for other programs and for the cache probe. */
  unsigned batches = d->received_stdin ? 4 : 32;
  if (d->instructions < batches * LIVE_WORK_BATCH)
    return false;
  *flags = DR_EMIT_STORE_TRANSLATIONS;
  return true;
}

#endif

static dr_emit_flags_t instrument_block(void *drcontext, void *tag,
                                        instrlist_t *bb, instr_t *where,
                                        bool for_trace, bool translating,
                                        void *user_data) {
  (void)tag;
  (void)for_trace;
  bool first_in_block = drmgr_is_first_instr(drcontext, where);
  dr_emit_flags_t flags = DR_EMIT_DEFAULT;
#ifdef DURANTE_TEST_COUNT_LOCATIONS
  (void)translating;
  bool inline_block = false;
#else
  bool inline_block = first_in_block && (cache_probe_enabled() || static_text_site(tag)) &&
                      select_inline_block(drcontext, bb, translating, &flags);
#endif
#ifdef DURANTE_L1D_PROBE
  if (probe_l1d && first_in_block)
    probe_inline_block = inline_block;
#endif
  if (instr_is_app(where) && instr_is_syscall(where) &&
      (instr_get_opcode(where) != OP_syscall ||
       instr_get_isa_mode(where) != DR_ISA_AMD64)) {
    /* This is a runtime clean call at the actual instruction. Merely having
     * unreachable compatibility bytes in a module must not taint a run. */
    dr_insert_clean_call_ex(drcontext, bb, where, (void *)unsupported_syscall_abi,
                           DR_CLEANCALL_READS_APP_CONTEXT |
                               DR_CLEANCALL_WRITES_APP_CONTEXT, 0);
  }
  if (first_in_block && user_data != NULL) {
    /* Preserve complete application state even when a signal handler reads
     * registers/flags that linear liveness analysis considers dead. */
    instr_t *first = instrlist_first_app(bb);
    uint64_t packed = (uint64_t)(ptr_uint_t)user_data;
    int count = (int)(uint32_t)packed;
    if (memory_operand_bytes_enabled)
      dr_insert_clean_call(drcontext, bb, where, (void *)count_block_memory, false, 4,
                           OPND_CREATE_INT32(count),
                           OPND_CREATE_INTPTR(first ? instr_get_app_pc(first) : NULL),
                           OPND_CREATE_INT32((int)(uint32_t)(packed >> 32)),
                           OPND_CREATE_INT32((int)suffix_memory_bytes(where)));
    else if (latency_bound_enabled)
      dr_insert_clean_call(drcontext, bb, where, (void *)count_block_latency, false, 3,
                           OPND_CREATE_INT32(count),
                           OPND_CREATE_INTPTR(first ? instr_get_app_pc(first) : NULL),
                           OPND_CREATE_INT32((int)(uint32_t)(packed >> 32)));
    else if (flat_inline)
      insert_flat_charge(drcontext, bb, where, count);
    else if (inline_block) {
      insert_charge(drcontext, bb, where, count, first ? instr_get_app_pc(first) : NULL);
      if (!cache_probe_enabled())
        flags = DR_EMIT_STORE_TRANSLATIONS;
    } else
      dr_insert_clean_call(drcontext, bb, where, (void *)count_block, false, 2,
                           OPND_CREATE_INT32(count),
                           OPND_CREATE_INTPTR(first ? instr_get_app_pc(first) : NULL));
  }
  if (instr_is_app(where) && (deny_avx || deny_simd)) {
    enum cpu_policy_decision decision = cpu_policy_classify(where, deny_avx, deny_simd);
    if (decision != CPU_POLICY_ALLOW)
      dr_insert_clean_call_ex(drcontext, bb, where, (void *)cpu_feature_violation,
                           DR_CLEANCALL_READS_APP_CONTEXT |
                               DR_CLEANCALL_WRITES_APP_CONTEXT,
                           1, OPND_CREATE_INT32(decision));
  }
  bool checkpoint = instr_is_app(where) && instr_writes_memory(where);
#ifdef DURANTE_L1D_PROBE
  if (probe_l1d && probe_instrument(drcontext, bb, where, checkpoint) &&
      !memory_operand_bytes_enabled)
    checkpoint = false;
#endif
  if (checkpoint) {
    app_pc pc = instr_get_app_pc(where);
    app_pc next_pc = pc ? pc + instr_length(drcontext, where) : NULL;
    int suffix = (int)write_suffix(bb, where);
    bool inline_write = cache_probe_enabled() || flat_inline;
#ifndef DURANTE_TEST_COUNT_LOCATIONS
    if (!inline_write && !latency_bound_enabled && static_text_site(pc))
      inline_write = select_inline_block(drcontext, bb, translating, &flags);
#endif
    if (memory_operand_bytes_enabled) {
      dr_insert_clean_call(drcontext, bb, where, (void *)before_write_memory, false, 5,
                           OPND_CREATE_INTPTR(pc), OPND_CREATE_INTPTR(next_pc),
                           OPND_CREATE_INT32(suffix),
                           OPND_CREATE_INT32((int)suffix_memory_bytes(where)),
                           OPND_CREATE_INT32((int)instruction_memory_bytes(where)));
    } else if (!latency_bound_enabled && inline_write) {
      insert_write_checkpoint(drcontext, bb, where, pc, next_pc, suffix);
      /* Flat layouts depend only on the block, so retranslation reproduces them. */
      if (!cache_probe_enabled() && !flat_inline)
        flags = DR_EMIT_STORE_TRANSLATIONS;
    } else
      dr_insert_clean_call(drcontext, bb, where, (void *)before_write, false, 3,
                           OPND_CREATE_INTPTR(pc), OPND_CREATE_INTPTR(next_pc),
                           OPND_CREATE_INT32(suffix));
  }
  if (rep_bytes_enabled && instr_is_app(where)) {
    const int op = instr_get_opcode(where);
    if (op == OP_rep_movs || op == OP_rep_stos) {
      int bytes = -1;
      reg_id_t count_reg = DR_REG_NULL;
      for (int i = 0; i < instr_num_dsts(where); ++i) {
        opnd_t dst = instr_get_dst(where, i);
        if (opnd_is_memory_reference(dst))
          bytes = opnd_size_in_bytes(opnd_get_size(dst));
      }
      for (int i = 0; i < instr_num_srcs(where); ++i) {
        opnd_t src = instr_get_src(where, i);
        if (opnd_is_reg(src) && (opnd_get_reg(src) == DR_REG_RCX ||
                                 opnd_get_reg(src) == DR_REG_ECX))
          count_reg = opnd_get_reg(src);
      }
      if (bytes != 1 && bytes != 2 && bytes != 4 && bytes != 8)
        fail("unknown REP MOVS/STOS element width");
      dr_insert_clean_call_ex(drcontext, bb, where, (void *)observe_rep,
                              DR_CLEANCALL_READS_APP_CONTEXT, 4,
                              OPND_CREATE_INTPTR(instr_get_app_pc(where)),
                              OPND_CREATE_INT32(op),
                              OPND_CREATE_INT32(bytes),
                              OPND_CREATE_INT32(count_reg));
    } else if (op == OP_rep_cmps || op == OP_repne_cmps || op == OP_rep_scas ||
               op == OP_repne_scas || op == OP_rep_lods || op == OP_rep_ins ||
               op == OP_rep_outs) {
      dr_insert_clean_call_ex(drcontext, bb, where, (void *)observe_rep,
                              DR_CLEANCALL_READS_APP_CONTEXT, 4,
                              OPND_CREATE_INTPTR(instr_get_app_pc(where)),
                              OPND_CREATE_INT32(op),
                              OPND_CREATE_INT32(0),
                              OPND_CREATE_INT32(DR_REG_NULL));
    }
  }
  return flags;
}

static bool lifecycle_syscall(int number) {
  return x32_syscall(number) ||
         number == SYS_fork || number == SYS_vfork || number == SYS_clone ||
         number == SYS_execve || number == SYS_exit || number == SYS_exit_group
#ifdef SYS_clone3
         || number == SYS_clone3
#endif
#ifdef SYS_execveat
         || number == SYS_execveat
#endif
      ;
}
static bool filter_syscall(void *ctx, int number) {
  (void)ctx;
  return live_work || lifecycle_syscall(number);
}
/* Only the ordinary static-image live clock initializes static_text_end. */
static bool static_input_syscall(int number) {
  return static_text_end && (number == SYS_read || number == SYS_readv);
}
static bool pre_syscall(void *ctx, int number) {
  if (static_text_end && !__atomic_load_n(&static_text_revoked, __ATOMIC_ACQUIRE) &&
      (x32_syscall(number) || changes_static_text(ctx, number)))
    __atomic_store_n(&static_text_revoked, true, __ATOMIC_RELEASE);
  thread_data_t *d = drmgr_get_tls_field(ctx, tls_index);
  if (!d)
    fail("syscall without TLS");
  if (live_work && !lifecycle_syscall(number)) {
    if (static_input_syscall(number))
      d->pending_stdin_read = !d->child_pid && dr_syscall_get_param(ctx, 0) == STDIN_FILENO;
    /* An intercepted syscall terminates its basic block. All prior charges
     * have passed replay correction and the syscall itself has been reached.
     * Flush before it can block indefinitely, even below the usual batch size.
     * Ordinary syscalls have no process lifecycle state to update. */
    publish_live_work(d, true);
    return true;
  }
  /* These transitions can escape the native x86-64 client. Reject them before
   * kernel entry or lifecycle bookkeeping; no process/image was created. */
  if ((deny_avx || deny_simd) && (x32_syscall(number) || number == SYS_execve
#ifdef SYS_execveat
                                || number == SYS_execveat
#endif
                                )) {
    dr_syscall_set_result(ctx, (reg_t)-EPERM);
    return false;
  }
  if (x32_syscall(number))
    unsupported_syscall_abi();
  bool unsupported = false;
  bool spawn = number == SYS_fork || number == SYS_vfork;
  bool exec = number == SYS_execve;
#ifdef SYS_execveat
  exec = exec || number == SYS_execveat;
#endif
  uint64_t flags = 0;
  if (number == SYS_clone)
    flags = (uint64_t)dr_syscall_get_param(ctx, 0);
#ifdef SYS_clone3
  if (number == SYS_clone3) {
    const void *args = (const void *)dr_syscall_get_param(ctx, 0);
    size_t size = (size_t)dr_syscall_get_param(ctx, 1);
    if (size < sizeof(flags) ||
        !dr_safe_read(args, sizeof(flags), &flags, NULL))
      unsupported = true;
  }
#endif
  if (number == SYS_clone
#ifdef SYS_clone3
      || number == SYS_clone3
#endif
  ) {
    spawn = (flags & CLONE_THREAD_FLAG) == 0;
    /* Non-thread shared-address-space clones have no isolated TLS accounting.
     */
    if (spawn && (flags & CLONE_VM_FLAG) && !(flags & CLONE_VFORK_FLAG))
      unsupported = true;
    /* The runtime fix restores cleared handlers for shared-VM spawn children.
     * Fork-style clone3 uses a separate signal initialization path. */
    if (spawn && (flags & CLONE_CLEAR_SIGHAND_FLAG) && !(flags & CLONE_VM_FLAG))
      unsupported = true;
  }
  if (d->child_pid && spawn)
    unsupported = true;
#ifdef DURANTE_L1D_PROBE
  if (probe_l1d && (spawn || exec))
    fail("cache probe requires a single process and image");
#endif
  if (spawn && (flags & CLONE_FILES_FLAG))
    unsupported = true;
  if ((deny_avx || deny_simd) && unsupported) {
    dr_syscall_set_result(ctx, (reg_t)-EPERM);
    return false;
  }
  if (unsupported)
    invalidate_live_work();
  d->pending_unsupported = unsupported;
  d->pending_syscall = number;
  d->pending_clone_flags = flags;
  d->pending_spawn = spawn;
  d->pending_exec = exec;
  dr_mutex_lock(state_lock);
  if (live_work) {
    if ((exec || number == SYS_exit_group) && !d->child_pid &&
        threads_started != threads_ended + 1)
      invalidate_live_work();
    publish_live_work(d, true);
  }
  if (number == SYS_exit_group ||
      (number == SYS_exit &&
       (d->child_pid || threads_started == threads_ended + 1))) {
    if (d->child_pid) {
      d->child_exit_seen = true;
      d->child_exit_code = (int)dr_syscall_get_param(ctx, 0) & 255;
    } else {
      app_exit_seen = true;
      app_exit_code = (int)dr_syscall_get_param(ctx, 0) & 255;
    }
  }
  if (exec)
    snapshot_event("exec_attempt", ctx, true);
  if (d->child_pid && d->child_exit_seen && !d->child_policy_exit)
    snapshot_event("end", ctx, false);
  if (d->child_pid && (exec || d->child_exit_seen) && d->child_file >= 0) {
    dr_close_file(d->child_file);
    d->child_file = -1;
  }
  dr_mutex_unlock(state_lock);
  return true;
}
static void post_syscall(void *ctx, int number) {
  bool ordinary = live_work && !lifecycle_syscall(number);
  if (ordinary && !static_input_syscall(number))
    return;
  thread_data_t *d = drmgr_get_tls_field(ctx, tls_index);
  if (!d)
    fail("post syscall without TLS");
  ptr_int_t result = (ptr_int_t)dr_syscall_get_result(ctx);
  if (ordinary) {
    if (d->pending_stdin_read && result > 0)
      d->received_stdin = true;
    d->pending_stdin_read = false;
    return;
  }
  dr_mutex_lock(state_lock);
  if (d->pending_spawn && result > 0) {
    char fields[192];
    dr_snprintf(fields, sizeof(fields),
                ",\"child_pid\":%d,\"syscall\":%d,\"supported\":%s",
                (int)result, number, d->pending_unsupported ? "false" : "true");
    route_event(d, "spawn", fields);
  }
  if (d->pending_exec) {
    char fields[96];
    dr_snprintf(fields, sizeof(fields), ",\"syscall\":%d,\"result\":%lld",
                number, (long long)result);
    route_event(d, "exec_failed", fields);
  }
  d->pending_syscall = -1;
  d->pending_spawn = false;
  d->pending_exec = false;
  d->pending_unsupported = false;
  dr_mutex_unlock(state_lock);
}
static dr_signal_action_t signal_event(void *ctx, dr_siginfo_t *info) {
  (void)info;
  thread_data_t *d = drmgr_get_tls_field(ctx, tls_index);
  if (!d || d->signals == UINT64_MAX)
    fail("signal accounting overflow");
  invalidate_live_work();
  ++d->signals;
  return DR_SIGNAL_DELIVER;
}
static void fork_init(void *ctx) {
  thread_data_t *inherited = drmgr_get_tls_field(ctx, tls_index);
  if (inherited && (inherited->pending_clone_flags & CLONE_FILES_FLAG))
    fail("shared-descriptor process clones are unsupported");
  process_id_t parent = owner_pid;
  char previous[80];
  memcpy(previous, segment, sizeof(previous));
  if (report_file >= 0)
    dr_close_file(report_file);
  report_file = -1;
  /* DR synchronizes threads before fork, and pre_syscall releases our mutex
   * before the syscall. Keep the inherited mutex so process_exit frees it;
   * replacing it leaks its allocation in every forked child. Completed and
   * other-thread counters must still be reset. DR restores surviving TLS. */
  owner_pid = dr_get_process_id();
  total_instructions = total_signals = total_rep_bytes = total_rep_entries =
      total_unsupported_rep_entries = total_latency_bound_x16 = total_memory_operand_bytes = 0;
  total_rep_movs_bytes = total_rep_stos_bytes = total_rep_movs_entries =
      total_rep_stos_entries = 0;
  threads_started = 1;
  threads_ended = event_sequence = 0;
  accounting_error = app_exit_seen = report_written = false;
  __atomic_store_n(&replay_without_tls, 0, __ATOMIC_RELAXED);
  app_exit_code = 0;
  thread_data_t *d = drmgr_get_tls_field(ctx, tls_index);
  if (!d)
    fail("fork without surviving TLS");
  memset(d, 0, sizeof(*d));
  set_raw_accounting_state(d);
  if (live_sampler) {
    /* Client threads do not survive fork; DR synchronized them before it. */
    sample_head = d;
    if (!dr_create_client_thread(sample_threads, NULL))
      fail("cannot start the live work sampler");
  }
  open_stream("fork", parent, previous);
}
static void pre_detach(void) {
  invalidate_live_work();
  dr_mutex_lock(state_lock);
  event("detached", "");
  dr_mutex_unlock(state_lock);
}
static void process_exit(void) {
  if (dr_get_process_id() != owner_pid)
    return;
  dr_mutex_lock(state_lock);
  if (!report_written) {
    snapshot_event("end", NULL, false);
    report_written = true;
  }
  if (report_file >= 0) {
    dr_close_file(report_file);
    report_file = -1;
  }
  dr_mutex_unlock(state_lock);
#ifdef DURANTE_TEST_COUNT_LOCATIONS
  profile_dump();
  drmgr_unregister_module_unload_event(profile_module_unload);
#endif
  drmgr_unregister_bb_instrumentation_event(analyze_block);
  if (durante_cpuid_active != DURANTE_CPUID_NONE)
    drmgr_unregister_bb_app2app_event(durante_cpuid_app2app);
  drmgr_unregister_thread_init_event(thread_init);
  drmgr_unregister_thread_exit_event(thread_exit);
  drmgr_unregister_pre_syscall_event(pre_syscall);
  drmgr_unregister_post_syscall_event(post_syscall);
  drmgr_unregister_signal_event(signal_event);
  drmgr_unregister_kernel_xfer_event(kernel_xfer);
  dr_unregister_durante_code_replay_event(code_replay);
  if (raw_tls_enabled) {
    drmgr_unregister_restore_state_ex_event(restore_state);
    dr_raw_tls_cfree(raw_tls_offset, RAW_TLS_SLOTS);
  }
  drmgr_unregister_tls_field(tls_index);
#ifdef DURANTE_L1D_PROBE
  if (probe_l1d) {
    if (probe_partial_block || total_signals || !app_exit_seen || accounting_error)
      dr_fprintf(STDERR, "durante: incomplete cache probe measurement\n");
    else
      probe_exit();
  }
#endif
  drmgr_exit();
  dr_mutex_destroy(state_lock);
  for (int i = 0; i <= FLAT_ENTRY_CACHE; ++i) {
    if (flat_entries[i])
      dr_global_free(flat_entries[i], sizeof(*flat_entries[i]));
  }
  if (checkpoint_template)
    dr_global_free(checkpoint_template, sizeof(*checkpoint_template));
  if (live_work)
    dr_unmap_file(live_work, sizeof(*live_work));
  if (cpu_policy_exit_pc)
    dr_raw_mem_free(cpu_policy_exit_pc, dr_page_size());
}
DR_EXPORT void dr_client_main(client_id_t id, int argc, const char *argv[]) {
  (void)id;
  dr_set_client_name("Durante process-tree BB work",
                     "https://dynamorio.org/");
  if (dr_durante_runtime_abi_version() != DR_DURANTE_RUNTIME_ABI_VERSION)
    fail("prepared runtime with code replay ABI 1 required");
  uint64 ignore_eflags;
  if (!dr_get_integer_option("cleancall_ignore_eflags", &ignore_eflags) || ignore_eflags)
    fail("prepared runtime with full clean-call flag preservation required");
  if (argc < 3 || strcmp(argv[1], "-outdir"))
    fail("usage: -c libdurante.so -outdir NEW_PRIVATE_DIRECTORY [-rep_bytes] "
         "[-disallow_cpu_feature avx|simd] [-cpuid_profile x86-64-v1|x86-64-v2] "
         "[-latency_bound [-memory_operand_bytes]] [-live_work_v1 OWNER_FILE] -- program");
  if (strlen(argv[2]) >= sizeof(outdir) || argv[2][0] != '/')
    fail("report directory must be absolute");
  memcpy(outdir, argv[2], strlen(argv[2]) + 1);
  const char *live_path = NULL;
  enum durante_cpuid_profile cpuid_profile = DURANTE_CPUID_NONE;
  for (int i = 3; i < argc; ++i) {
    if (strcmp(argv[i], "-rep_bytes") == 0 && !rep_bytes_enabled) {
      rep_bytes_enabled = true;
#ifdef DURANTE_L1D_PROBE
    } else if (strcmp(argv[i], "-probe_l1d") == 0 && !probe_l1d) {
      probe_l1d = true;
#endif
    } else if (strcmp(argv[i], "-live_work_v1") == 0 && !live_path && i + 1 < argc) {
      live_path = argv[++i];
    } else if (strcmp(argv[i], "-latency_bound") == 0 && !latency_bound_enabled) {
      latency_bound_enabled = true;
    } else if (strcmp(argv[i], "-memory_operand_bytes") == 0 && !memory_operand_bytes_enabled) {
      memory_operand_bytes_enabled = true;
    } else if (strcmp(argv[i], "-disallow_cpu_feature") == 0 && i + 1 < argc) {
      const char *feature = argv[++i];
      if (strcmp(feature, "avx") == 0 && !deny_avx)
        deny_avx = true;
      else if (strcmp(feature, "simd") == 0 && !deny_simd)
        deny_simd = true;
      else
        fail("unknown or duplicate disallowed CPU feature");
    } else if (strcmp(argv[i], "-cpuid_profile") == 0 && i + 1 < argc &&
               cpuid_profile == DURANTE_CPUID_NONE) {
      cpuid_profile = durante_cpuid_profile_parse(argv[++i]);
      if (cpuid_profile == DURANTE_CPUID_NONE)
        fail("unknown CPUID profile");
    } else {
      fail("unknown or duplicate client option");
    }
  }
  if (memory_operand_bytes_enabled && !latency_bound_enabled)
    fail("memory operand bytes require -latency_bound");
  if (live_path) {
    if (!rep_bytes_enabled)
      fail("live work requires -rep_bytes");
    open_live_work(live_path);
  }
  owner_pid = dr_get_process_id();
  if (cpuid_profile != DURANTE_CPUID_NONE) {
    /* Check the real processor before the first application instruction. */
    const char *missing = durante_cpuid_enable(cpuid_profile, cpuid_request_invalid);
    if (missing)
      fail(missing);
  }
  if (deny_avx || deny_simd) {
    cpu_policy_exit_pc = dr_raw_mem_alloc(dr_page_size(),
                                         DR_MEMPROT_READ | DR_MEMPROT_WRITE, NULL);
    if (!cpu_policy_exit_pc)
      fail("cannot allocate CPU policy exit");
    /* syscall; ud2. This page is translated as application code so the runtime
     * observes SYS_exit_group and restores shared-VM child lifecycle state. */
    memcpy(cpu_policy_exit_pc, "\x0f\x05\x0f\x0b", 4);
    if (!dr_memory_protect(cpu_policy_exit_pc, dr_page_size(),
                          DR_MEMPROT_READ | DR_MEMPROT_EXEC))
      fail("cannot protect CPU policy exit");
  }
  if (!drmgr_init())
    fail("extension initialization failed");
  if (live_work && !latency_bound_enabled && !cache_probe_enabled()) {
    module_data_t *main = dr_get_main_module();
    if (main) {
      find_static_text(main);
      dr_free_module_data(main);
    }
  }
#ifdef DURANTE_TEST_COUNT_LOCATIONS
  if (!drmgr_register_module_unload_event(profile_module_unload))
    fail("test module lifetime registration failed");
#endif
  state_lock = dr_mutex_create();
#ifdef DURANTE_TEST_COUNT_LOCATIONS
  const bool flat_candidate = false; /* The location profile needs entry callbacks. */
#else
  const bool flat_candidate = !latency_bound_enabled && !cache_probe_enabled();
#endif
  raw_tls_enabled = (static_text_end != 0 || flat_candidate) &&
                    dr_raw_tls_calloc(&raw_tls_register, &raw_tls_offset, RAW_TLS_SLOTS, 0);
  flat_inline = flat_candidate && raw_tls_enabled;
  live_sampler = flat_inline && live_work;
  if (live_sampler && !(sample_lock = dr_mutex_create()))
    fail("cannot create the live work sampler lock");
  tls_index = drmgr_register_tls_field();
  if (!state_lock || tls_index < 0 ||
      (cpuid_profile != DURANTE_CPUID_NONE &&
       !drmgr_register_bb_app2app_event(durante_cpuid_app2app, NULL)) ||
      !drmgr_register_thread_init_event(thread_init) ||
      !drmgr_register_thread_exit_event(thread_exit) ||
      !drmgr_register_bb_instrumentation_event(analyze_block, instrument_block,
                                               NULL) ||
      !drmgr_register_pre_syscall_event(pre_syscall) ||
      !drmgr_register_post_syscall_event(post_syscall) ||
      !drmgr_register_signal_event(signal_event) ||
      !drmgr_register_kernel_xfer_event(kernel_xfer) ||
      (raw_tls_enabled && !drmgr_register_restore_state_ex_event(restore_state)))
    fail("event registration failed");
  dr_register_durante_code_replay_event(code_replay);
  open_stream("initial", 0, "");
  dr_register_filter_syscall_event(filter_syscall);
  dr_register_fork_init_event(fork_init);
  dr_register_pre_detach_event(pre_detach);
  dr_register_exit_event(process_exit);
  if (live_sampler && !dr_create_client_thread(sample_threads, NULL))
    fail("cannot start the live work sampler");
  if (live_work)
    __atomic_store_n(&live_work->ready, 1, __ATOMIC_RELEASE);
}

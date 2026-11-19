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
#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
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
} thread_data_t;
static int tls_index;
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
static bool latency_bound_enabled;
static uint64_t total_latency_bound_x16;
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
static int app_exit_code;
static size_t file_bytes;
#ifdef DURANTE_TEST_COUNT_LOCATIONS
#include "../tests/count_locations/profile.h"
#endif

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

static void publish_live_work(thread_data_t *d, bool flush) {
  if (d->signals || d->rep_error || d->replay_error || d->cpuid_error ||
      d->counter_overflows || d->unsupported_rep_entries ||
      d->instructions < d->published_instructions ||
      d->rep_movs_bytes < d->published_movs || d->rep_stos_bytes < d->published_stos) {
    invalidate_live_work();
    return;
  }
  if (d->restart_pc) {
    if (flush)
      invalidate_live_work();
    return;
  }
  uint64_t instructions = d->instructions - d->published_instructions;
  uint64_t movs = d->rep_movs_bytes - d->published_movs;
  uint64_t stos = d->rep_stos_bytes - d->published_stos;
  /* Omit the current BB's speculative entry charge. Prior BBs have passed
   * the runtime's replay correction boundary. Flush small deltas at syscalls
   * which may retire the thread or replace the image. */
  if (!flush && (instructions | movs | stos) < 4096)
    return;
  add_live_delta(&live_work->instructions, instructions, 1);
  add_live_delta(&live_work->movs, movs, 2);
  add_live_delta(&live_work->stos, stos, 4);
  d->published_instructions = d->instructions;
  d->published_movs = d->rep_movs_bytes;
  d->published_stos = d->rep_stos_bytes;
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
              "version\":\"11.3.0\"%s%s%s",
              origin, (int)parent, parent_segment,
              rep_bytes_enabled ? "true" : "false", policy_fields, cpuid_fields,
              latency_fields);
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
  if (!d || d->rep_error || d->replay_error || d->cpuid_error || d->restart_pc ||
      d->counter_overflows || d->latency_error ||
      UINT64_MAX - total_instructions < d->instructions ||
      UINT64_MAX - total_latency_bound_x16 < d->latency_bound_x16 ||
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
  bool child = d && d->child_pid;
  uint64_t instructions = child ? 0 : total_instructions,
           latency = child ? 0 : total_latency_bound_x16,
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
  route_event(d, kind, fields);
}

static void thread_init(void *ctx) {
  thread_data_t *d = dr_thread_alloc(ctx, sizeof(*d));
  if (!d)
    fail("thread allocation failed");
  memset(d, 0, sizeof(*d));
  d->child_file = -1;
  if (!drmgr_set_tls_field(ctx, tls_index, d))
    fail("TLS init failed");
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
      (d->instructions != d->published_instructions ||
       d->rep_movs_bytes != d->published_movs || d->rep_stos_bytes != d->published_stos))
    invalidate_live_work();
  if (d && d->child_pid) {
    /* This callback can run in the parent after child exec. Its fd number
     * refers to the child's descriptor table, so NEVER close that number in the
     * parent. Exec/_exit checkpoints close it in child context before the
     * transition. */
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

static thread_data_t *charge_block(int instructions, app_pc first_pc) {
  thread_data_t *data = drmgr_get_tls_field(dr_get_current_drcontext(), tls_index);
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

/* The bound is charged at the same point as the count, so a block's bound is
 * counted exactly when its instructions are. */
static void count_block_latency(int instructions, app_pc first_pc, int bound_x16) {
  thread_data_t *data = charge_block(instructions, first_pc);
  if (data == NULL)
    return;
  data->block_instructions = (uint32_t)instructions;
  data->block_bound_x16 = (uint32_t)bound_x16;
  if (bound_x16 < 0)
    counter_error(&data->latency_error);
  else if (UINT64_MAX - data->latency_bound_x16 < (uint64_t)bound_x16)
    data->counter_overflows = 1;
  else
    data->latency_bound_x16 += (uint64_t)bound_x16;
}

/* A fault can leave the middle of a BB whose entire length was charged at
 * entry. Save its unexecuted suffix at the write, not a whole-counter snapshot:
 * already executed prefixes and intervening signal work must remain charged.
 */
static void before_write(app_pc pc, app_pc next_pc, int suffix) {
  thread_data_t *data = drmgr_get_tls_field(dr_get_current_drcontext(), tls_index);
  if (data == NULL)
    fail("write checkpoint without thread state");
  data->write_pc = pc;
  data->write_next_pc = next_pc;
  data->write_suffix = suffix > 0 ? (uint64_t)suffix : 0;
  data->write_rep = false;
  /* The write belongs to the block most recently charged in this thread. */
  data->write_block_instructions = data->block_instructions;
  data->write_block_bound_x16 = data->block_bound_x16;
}

static void code_replay(void *ctx, int reason, app_pc source_pc,
                        app_pc target_pc) {
  thread_data_t *d = drmgr_get_tls_field(ctx, tls_index);
  if (!d) {
    invalidate_live_work();
    __atomic_store_n(&replay_without_tls, 1, __ATOMIC_RELAXED);
    return;
  }
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
  d->instructions -= rollback;
#ifdef DURANTE_TEST_COUNT_LOCATIONS
  profile_replay(rollback);
#endif
  if (retry) {
    d->restart_pc = target_pc;
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

static dr_emit_flags_t instrument_block(void *drcontext, void *tag,
                                        instrlist_t *bb, instr_t *where,
                                        bool for_trace, bool translating,
                                        void *user_data) {
  (void)tag;
  (void)for_trace;
  (void)translating;
  if (instr_is_app(where) && instr_is_syscall(where) &&
      (instr_get_opcode(where) != OP_syscall ||
       instr_get_isa_mode(where) != DR_ISA_AMD64)) {
    /* This is a runtime clean call at the actual instruction. Merely having
     * unreachable compatibility bytes in a module must not taint a run. */
    dr_insert_clean_call_ex(drcontext, bb, where, (void *)unsupported_syscall_abi,
                           DR_CLEANCALL_READS_APP_CONTEXT |
                               DR_CLEANCALL_WRITES_APP_CONTEXT, 0);
  }
  if (drmgr_is_first_instr(drcontext, where) && user_data != NULL) {
    /* Preserve complete application state even when a signal handler reads
     * registers/flags that linear liveness analysis considers dead. */
    instr_t *first = instrlist_first_app(bb);
    uint64_t packed = (uint64_t)(ptr_uint_t)user_data;
    int count = (int)(uint32_t)packed;
    if (latency_bound_enabled)
      dr_insert_clean_call(drcontext, bb, where, (void *)count_block_latency, false, 3,
                           OPND_CREATE_INT32(count),
                           OPND_CREATE_INTPTR(first ? instr_get_app_pc(first) : NULL),
                           OPND_CREATE_INT32((int)(uint32_t)(packed >> 32)));
    else
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
  if (instr_is_app(where) && instr_writes_memory(where)) {
    app_pc pc = instr_get_app_pc(where);
    app_pc next_pc = pc ? pc + instr_length(drcontext, where) : NULL;
    dr_insert_clean_call(drcontext, bb, where, (void *)before_write, false, 3,
                         OPND_CREATE_INTPTR(pc), OPND_CREATE_INTPTR(next_pc),
                         OPND_CREATE_INT32((int)write_suffix(bb, where)));
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
  return DR_EMIT_DEFAULT;
}

static bool filter_syscall(void *ctx, int number) {
  (void)ctx;
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
static bool pre_syscall(void *ctx, int number) {
  thread_data_t *d = drmgr_get_tls_field(ctx, tls_index);
  if (!d)
    fail("syscall without TLS");
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
  if (live_work && (exec || number == SYS_exit_group || number == SYS_exit)) {
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
  thread_data_t *d = drmgr_get_tls_field(ctx, tls_index);
  if (!d)
    fail("post syscall without TLS");
  ptr_int_t result = (ptr_int_t)dr_syscall_get_result(ctx);
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
      total_unsupported_rep_entries = total_latency_bound_x16 = 0;
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
  drmgr_unregister_tls_field(tls_index);
  drmgr_exit();
  dr_mutex_destroy(state_lock);
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
  if (argc < 3 || strcmp(argv[1], "-outdir"))
    fail("usage: -c libdurante.so -outdir NEW_PRIVATE_DIRECTORY [-rep_bytes] "
         "[-disallow_cpu_feature avx|simd] [-cpuid_profile x86-64-v1|x86-64-v2] "
         "[-latency_bound] [-live_work_v1 OWNER_FILE] -- program");
  if (strlen(argv[2]) >= sizeof(outdir) || argv[2][0] != '/')
    fail("report directory must be absolute");
  memcpy(outdir, argv[2], strlen(argv[2]) + 1);
  const char *live_path = NULL;
  enum durante_cpuid_profile cpuid_profile = DURANTE_CPUID_NONE;
  for (int i = 3; i < argc; ++i) {
    if (strcmp(argv[i], "-rep_bytes") == 0 && !rep_bytes_enabled) {
      rep_bytes_enabled = true;
    } else if (strcmp(argv[i], "-live_work_v1") == 0 && !live_path && i + 1 < argc) {
      live_path = argv[++i];
    } else if (strcmp(argv[i], "-latency_bound") == 0 && !latency_bound_enabled) {
      latency_bound_enabled = true;
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
#ifdef DURANTE_TEST_COUNT_LOCATIONS
  if (!drmgr_register_module_unload_event(profile_module_unload))
    fail("test module lifetime registration failed");
#endif
  state_lock = dr_mutex_create();
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
      !drmgr_register_kernel_xfer_event(kernel_xfer))
    fail("event registration failed");
  dr_register_durante_code_replay_event(code_replay);
  open_stream("initial", 0, "");
  dr_register_filter_syscall_event(filter_syscall);
  dr_register_fork_init_event(fork_init);
  dr_register_pre_detach_event(pre_detach);
  dr_register_exit_event(process_exit);
  if (live_work)
    __atomic_store_n(&live_work->ready, 1, __ATOMIC_RELEASE);
}

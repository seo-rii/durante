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
#include "drreg.h"
#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
#ifndef X86_64
#error "workmeter supports Linux x86-64 only"
#endif
#define CLONE_THREAD_FLAG UINT64_C(0x00010000)
#define CLONE_VM_FLAG UINT64_C(0x00000100)
#define CLONE_VFORK_FLAG UINT64_C(0x00004000)
#define CLONE_FILES_FLAG UINT64_C(0x00000400)
#define MAX_STREAM_BYTES (1024 * 1024)
#define MAX_EVENTS 4096

typedef struct {
  uint64_t instructions, counter_overflows, signals, rep_bytes, rep_entries,
      unsupported_rep_entries;
  bool rep_error;
  bool pending_spawn, pending_exec, pending_unsupported;
  int pending_syscall;
  uint64_t pending_clone_flags;
  process_id_t child_pid;
  char child_segment[80];
  int child_file, child_exit_code;
  size_t child_bytes;
  uint64_t child_sequence;
  bool child_exit_seen;
} thread_data_t;
static int tls_index;
static void *state_lock;
static int report_file = -1;
static process_id_t owner_pid;
static char outdir[4096], segment[80];
static uint64_t total_instructions, total_signals, total_rep_bytes,
    total_rep_entries, total_unsupported_rep_entries, threads_started,
    threads_ended, event_sequence;
static bool rep_bytes_enabled, accounting_error, app_exit_seen, report_written;
static int app_exit_code;
static size_t file_bytes;

static void fail(const char *m) {
  dr_fprintf(STDERR, "workmeter: %s\n", m);
  dr_abort();
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
    n = write(fd, buffer, (size_t)length);
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
      O_WRONLY | O_CREAT | O_EXCL | O_APPEND | O_NOFOLLOW | O_CLOEXEC, 0600);
  if (fd < 0 && errno == EEXIST)
    fd = openat(dir, name, O_WRONLY | O_APPEND | O_NOFOLLOW | O_CLOEXEC);
  close(dir);
  struct stat st;
  if (fd < 0 || fstat(fd, &st) != 0 || !S_ISREG(st.st_mode) ||
      st.st_nlink != 1 || st.st_uid != geteuid() || st.st_size < 0 ||
      st.st_size >= MAX_STREAM_BYTES)
    fail("unsafe or oversized report stream");
  *bytes = (size_t)st.st_size;
  return fd;
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
  char fields[256];
  dr_snprintf(fields, sizeof(fields),
              ",\"origin\":\"%s\",\"parent_pid\":%d,"
              "\"parent_segment\":\"%s\",\"rep_bytes_enabled\":%s,\"sdk_"
              "version\":\"11.3.0\"",
              origin, (int)parent, parent_segment,
              rep_bytes_enabled ? "true" : "false");
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
  if (!d || d->rep_error || d->counter_overflows ||
      UINT64_MAX - total_instructions < d->instructions ||
      UINT64_MAX - total_signals < d->signals ||
      UINT64_MAX - total_rep_bytes < d->rep_bytes ||
      UINT64_MAX - total_rep_entries < d->rep_entries ||
      UINT64_MAX - total_unsupported_rep_entries < d->unsupported_rep_entries)
    return false;
  total_instructions += d->instructions;
  total_signals += d->signals;
  total_rep_bytes += d->rep_bytes;
  total_rep_entries += d->rep_entries;
  total_unsupported_rep_entries += d->unsupported_rep_entries;
  return true;
}

static void snapshot_event(const char *kind, void *ctx, bool is_exec) {
  thread_data_t *d = ctx ? drmgr_get_tls_field(ctx, tls_index) : NULL;
  bool child = d && d->child_pid;
  uint64_t instructions = child ? 0 : total_instructions,
           signals = child ? 0 : total_signals,
           bytes = child ? 0 : total_rep_bytes,
           entries = child ? 0 : total_rep_entries,
           unsupported = child ? 0 : total_unsupported_rep_entries;
  uint64_t started = child ? 1 : threads_started,
           ended = child ? 0 : threads_ended;
  bool error = child ? false : accounting_error;
  if (is_exec || child) {
    error = error || !d || d->rep_error || d->counter_overflows ||
            started != ended + 1;
    if (d) {
      if (UINT64_MAX - instructions < d->instructions ||
          UINT64_MAX - signals < d->signals ||
          UINT64_MAX - bytes < d->rep_bytes ||
          UINT64_MAX - entries < d->rep_entries ||
          UINT64_MAX - unsupported < d->unsupported_rep_entries)
        error = true;
      else {
        instructions += d->instructions;
        signals += d->signals;
        bytes += d->rep_bytes;
        entries += d->rep_entries;
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
      "\"rep_entries\":%llu,\"unsupported_rep_entries\":%llu,\"signals_"
      "observed\":%llu,"
      "\"threads_started\":%llu,\"threads_finished\":%llu,\"threads_complete\":"
      "%s,"
      "\"accounting_error\":%s,\"app_exit_seen\":%s,\"app_exit_code\":%d,"
      "\"valid\":%s,\"quality\":\"%s\"",
      (unsigned long long)instructions, (unsigned long long)bytes,
      (unsigned long long)entries, (unsigned long long)unsupported,
      (unsigned long long)signals, (unsigned long long)started,
      (unsigned long long)ended, complete ? "true" : "false",
      error ? "true" : "false", exit_seen ? "true" : "false", exit_code,
      valid ? "true" : "false", signals ? "signals_observed" : "uninterrupted");
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
  if (!add_thread(d))
    accounting_error = true;
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
  (void)drcontext;
  (void)tag;
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
  *user_data = (void *)(ptr_uint_t)count;
  return DR_EMIT_DEFAULT;
}

/* Optional byte demand is deliberately separate from instruction count.  A
 * clean call only at REP MOVS/STOS observes the architectural repeat register
 * at that instruction, after any application instructions earlier in the BB.
 * Signals mark quality explicitly; requested bytes are not completed memory
 * transfers. CMPS/SCAS can stop early and
 * are counted as unsupported entries instead of charging their initial RCX.
 */
static void observe_rep(int bytes, int count_reg) {
  void *drcontext = dr_get_current_drcontext();
  thread_data_t *data = drmgr_get_tls_field(drcontext, tls_index);
  if (data == NULL)
    fail("REP observation without thread state");
  if (bytes == 0) {
    if (data->unsupported_rep_entries == UINT64_MAX)
      data->rep_error = true;
    else
      ++data->unsupported_rep_entries;
    return;
  }
  dr_mcontext_t mc = {0};
  mc.size = sizeof(mc);
  mc.flags = DR_MC_INTEGER;
  if (count_reg == DR_REG_NULL || !dr_get_mcontext(drcontext, &mc)) {
    data->rep_error = true;
    return;
  }
  uint64_t repeats = reg_get_value((reg_id_t)count_reg, &mc);
  if (repeats > UINT64_MAX / (unsigned)bytes ||
      UINT64_MAX - data->rep_bytes < repeats * (unsigned)bytes ||
      data->rep_entries == UINT64_MAX) {
    data->rep_error = true;
    return;
  }
  data->rep_bytes += repeats * (unsigned)bytes;
  ++data->rep_entries;
}

static dr_emit_flags_t instrument_block(void *drcontext, void *tag,
                                        instrlist_t *bb, instr_t *where,
                                        bool for_trace, bool translating,
                                        void *user_data) {
  (void)tag;
  (void)for_trace;
  (void)translating;
  if (drmgr_is_first_instr(drcontext, where) && user_data != NULL) {
    drmgr_disable_auto_predication(drcontext, bb);
    reg_id_t reg;
    if (drreg_reserve_aflags(drcontext, bb, where) != DRREG_SUCCESS ||
        drreg_reserve_register(drcontext, bb, where, NULL, &reg) !=
            DRREG_SUCCESS)
      fail("cannot reserve instrumentation register/flags");
    if (!drmgr_insert_read_tls_field(drcontext, tls_index, bb, where, reg))
      fail("cannot load thread-local counter");
    instrlist_meta_preinsert(
        bb, where,
        INSTR_CREATE_add(
            drcontext,
            opnd_create_base_disp(reg, DR_REG_NULL, 0,
                                  (int)offsetof(thread_data_t, instructions),
                                  OPSZ_8),
            OPND_CREATE_INT32((int)(ptr_uint_t)user_data)));
    /* Preserve carry before restoring application flags. This extra checked
     * addition prevents a wrapped per-thread counter becoming a valid total. */
    instrlist_meta_preinsert(
        bb, where,
        INSTR_CREATE_adc(drcontext,
                         opnd_create_base_disp(
                             reg, DR_REG_NULL, 0,
                             (int)offsetof(thread_data_t, counter_overflows),
                             OPSZ_8),
                         OPND_CREATE_INT32(0)));
    if (drreg_unreserve_register(drcontext, bb, where, reg) != DRREG_SUCCESS ||
        drreg_unreserve_aflags(drcontext, bb, where) != DRREG_SUCCESS)
      fail("cannot restore instrumentation register/flags");
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
                              DR_CLEANCALL_READS_APP_CONTEXT, 2,
                              OPND_CREATE_INT32(bytes),
                              OPND_CREATE_INT32(count_reg));
    } else if (op == OP_rep_cmps || op == OP_repne_cmps || op == OP_rep_scas ||
               op == OP_repne_scas || op == OP_rep_lods || op == OP_rep_ins ||
               op == OP_rep_outs) {
      dr_insert_clean_call_ex(drcontext, bb, where, (void *)observe_rep,
                              DR_CLEANCALL_READS_APP_CONTEXT, 2,
                              OPND_CREATE_INT32(0),
                              OPND_CREATE_INT32(DR_REG_NULL));
    }
  }
  return DR_EMIT_DEFAULT;
}

static bool filter_syscall(void *ctx, int number) {
  (void)ctx;
  return number == SYS_fork || number == SYS_vfork || number == SYS_clone ||
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
  d->pending_spawn = false;
  d->pending_exec = false;
  d->pending_unsupported = false;
  d->pending_syscall = number;
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
      d->pending_unsupported = true;
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
      d->pending_unsupported = true;
  }
  if (d->child_pid && spawn)
    d->pending_unsupported = true;
  d->pending_clone_flags = flags;
  if (spawn && (flags & CLONE_FILES_FLAG))
    d->pending_unsupported = true;
  d->pending_spawn = spawn;
  d->pending_exec = exec;
  dr_mutex_lock(state_lock);
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
  if (d->child_pid && d->child_exit_seen)
    snapshot_event("end", ctx, false);
  if (d->child_pid && (exec || d->child_exit_seen) && d->child_file >= 0) {
    close(d->child_file);
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
    close(report_file);
  report_file = -1;
  /* Mutex and all completed/other-thread state inherited from the parent must
   * not contribute to the child's work. DR restores the surviving TLS slot. */
  state_lock = dr_mutex_create();
  owner_pid = dr_get_process_id();
  total_instructions = total_signals = total_rep_bytes = total_rep_entries =
      total_unsupported_rep_entries = 0;
  threads_started = 1;
  threads_ended = event_sequence = 0;
  accounting_error = app_exit_seen = report_written = false;
  app_exit_code = 0;
  thread_data_t *d = drmgr_get_tls_field(ctx, tls_index);
  if (!d)
    fail("fork without surviving TLS");
  memset(d, 0, sizeof(*d));
  open_stream("fork", parent, previous);
}
static void pre_detach(void) {
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
    close(report_file);
    report_file = -1;
  }
  dr_mutex_unlock(state_lock);
  drmgr_unregister_bb_instrumentation_event(analyze_block);
  drmgr_unregister_thread_init_event(thread_init);
  drmgr_unregister_thread_exit_event(thread_exit);
  drmgr_unregister_pre_syscall_event(pre_syscall);
  drmgr_unregister_post_syscall_event(post_syscall);
  drmgr_unregister_signal_event(signal_event);
  drmgr_unregister_tls_field(tls_index);
  drreg_exit();
  drmgr_exit();
  dr_mutex_destroy(state_lock);
}
DR_EXPORT void dr_client_main(client_id_t id, int argc, const char *argv[]) {
  (void)id;
  dr_set_client_name("Workmeter process-tree BB work",
                     "https://dynamorio.org/");
  if ((argc != 3 && argc != 4) || strcmp(argv[1], "-outdir") ||
      (argc == 4 && strcmp(argv[3], "-rep_bytes")))
    fail("usage: -c libworkmeter.so -outdir NEW_PRIVATE_DIRECTORY [-rep_bytes] "
         "-- program");
  if (strlen(argv[2]) >= sizeof(outdir) || argv[2][0] != '/')
    fail("report directory must be absolute");
  memcpy(outdir, argv[2], strlen(argv[2]) + 1);
  rep_bytes_enabled = argc == 4;
  owner_pid = dr_get_process_id();
  drreg_options_t options = {0};
  options.struct_size = sizeof(options);
  options.num_spill_slots = 3;
  if (!drmgr_init() || drreg_init(&options) != DRREG_SUCCESS)
    fail("extension initialization failed");
  state_lock = dr_mutex_create();
  tls_index = drmgr_register_tls_field();
  if (!state_lock || tls_index < 0 ||
      !drmgr_register_thread_init_event(thread_init) ||
      !drmgr_register_thread_exit_event(thread_exit) ||
      !drmgr_register_bb_instrumentation_event(analyze_block, instrument_block,
                                               NULL) ||
      !drmgr_register_pre_syscall_event(pre_syscall) ||
      !drmgr_register_post_syscall_event(post_syscall) ||
      !drmgr_register_signal_event(signal_event))
    fail("event registration failed");
  open_stream("initial", 0, "");
  dr_register_filter_syscall_event(filter_syscall);
  dr_register_fork_init_event(fork_init);
  dr_register_pre_detach_event(pre_detach);
  dr_register_exit_event(process_exit);
}

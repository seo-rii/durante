/* Experimental sidecar for trusted single-thread, single-image fixtures.
 * The caller cross-checks these entry charges against the production client's
 * uninterrupted, single-process instruction receipt from the same execution. */
#include "dr_api.h"
#include "drmgr.h"
#include <stdint.h>
#include <string.h>

static uint64_t instructions, loads;
static volatile int threads;
static file_t output;
static process_id_t owner;

static void fail(const char *why) {
  dr_fprintf(STDERR, "load_probe: %s\n", why);
  dr_abort_with_code(1);
}

static void thread_init(void *drcontext) {
  (void)drcontext;
  if (dr_atomic_add32_return_sum(&threads, 1) != 1)
    fail("multiple threads");
}

static void fork_init(void *drcontext) {
  (void)drcontext;
  fail("multiple processes");
}

static void charge(uint32_t count, uint32_t reads) {
  if (UINT64_MAX - instructions < count || UINT64_MAX - loads < reads)
    fail("counter overflow");
  instructions += count;
  loads += reads;
}

static dr_emit_flags_t analyze(void *context, void *tag, instrlist_t *bb,
                               bool for_trace, bool translating, void **user_data) {
  (void)context;
  (void)tag;
  (void)for_trace;
  (void)translating;
  uint32_t count = 0, reads = 0;
  bool emulating = false;
  for (instr_t *ins = instrlist_first(bb); ins; ins = instr_get_next(ins)) {
    instr_t *original = ins;
    if (drmgr_is_emulation_start(ins)) {
      emulated_instr_t em = { .size = sizeof(em) };
      if (emulating || !drmgr_get_emulated_instr_data(ins, &em) || !em.instr)
        fail("invalid emulation start");
      original = em.instr;
      emulating = true;
    } else if (drmgr_is_emulation_end(ins)) {
      if (!emulating)
        fail("invalid emulation end");
      emulating = false;
      continue;
    } else if (emulating || !instr_is_app(ins)) {
      continue;
    }
    if (count == UINT32_MAX)
      fail("basic block exceeds supported length");
    ++count;
    if (instr_reads_memory(original))
      ++reads;
  }
  if (emulating)
    fail("unterminated emulation region");
  *user_data = (void *)(ptr_uint_t)(((uint64_t)reads << 32) | count);
  return DR_EMIT_DEFAULT;
}

static dr_emit_flags_t insert(void *context, void *tag, instrlist_t *bb, instr_t *where,
                              bool for_trace, bool translating, void *user_data) {
  (void)tag;
  (void)for_trace;
  (void)translating;
  if (drmgr_is_first_instr(context, where) && user_data) {
    uint64_t counts = (uint64_t)(ptr_uint_t)user_data;
    dr_insert_clean_call(context, bb, where, (void *)charge, false, 2,
                         OPND_CREATE_INT32((uint32_t)counts),
                         OPND_CREATE_INT32((uint32_t)(counts >> 32)));
  }
  return DR_EMIT_DEFAULT;
}

static void finish(void) {
  if (threads != 1 || owner != dr_get_process_id() || !instructions)
    fail("incomplete single-thread run");
  char record[192];
  int n = dr_snprintf(record, sizeof(record),
                      "{\"schema\":1,\"pid\":%u,\"threads\":1,\"instructions\":%llu,"
                      "\"load_instructions\":%llu}\n",
                      (unsigned)owner, (unsigned long long)instructions,
                      (unsigned long long)loads);
  if (n <= 0 || (size_t)n >= sizeof(record) ||
      dr_write_file(output, record, (size_t)n) != n)
    fail("cannot write report");
  dr_close_file(output);
  drmgr_unregister_bb_instrumentation_event(analyze);
  drmgr_unregister_thread_init_event(thread_init);
  drmgr_exit();
}

DR_EXPORT void dr_client_main(client_id_t id, int argc, const char *argv[]) {
  (void)id;
  if (argc != 3 || strcmp(argv[1], "-out") != 0)
    fail("expected -out <new file>");
  output = dr_open_file(argv[2], DR_FILE_WRITE_REQUIRE_NEW | DR_FILE_CLOSE_ON_FORK);
  if (output == INVALID_FILE)
    fail("cannot create a fresh report");
  owner = dr_get_process_id();
  if (!drmgr_init() || !drmgr_register_thread_init_event(thread_init) ||
      !drmgr_register_bb_instrumentation_event(analyze, insert, NULL))
    fail("cannot register instrumentation");
  dr_register_fork_init_event(fork_init);
  dr_register_exit_event(finish);
}

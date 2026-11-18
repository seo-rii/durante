/* Test-only client: the production static latency bound plus a basic-block
 * counter charged at block entry. It needs no Durante runtime patch, so it runs
 * on the stock SDK core and checks the shared analysis in isolation. */
#include "dr_api.h"
#include "drmgr.h"
#include <string.h>
#include "../../client/latency_bound.h"

static uint64_t instructions, bound_x16;
static int unanalyzable;
static char output[1024];

static dr_emit_flags_t analyze(void *drcontext, void *tag, instrlist_t *bb,
                               bool for_trace, bool translating, void **user_data) {
  (void)for_trace; (void)translating;
  uint count = 0;
  for (instr_t *ins = instrlist_first_app(bb); ins; ins = instr_get_next_app(ins))
    ++count;
  uint64_t bound = count ? durante_latency_bound_x16(drcontext, tag, bb, count) : 0;
  *user_data = (void *)(ptr_uint_t)((uint64_t)count | bound << 32);
  return DR_EMIT_DEFAULT;
}

static void add(int count, int bound) {
  __atomic_fetch_add(&instructions, (uint64_t)count, __ATOMIC_RELAXED);
  if (bound < 0)
    __atomic_store_n(&unanalyzable, 1, __ATOMIC_RELAXED);
  else
    __atomic_fetch_add(&bound_x16, (uint64_t)bound, __ATOMIC_RELAXED);
}

static dr_emit_flags_t insert(void *drcontext, void *tag, instrlist_t *bb,
                              instr_t *where, bool for_trace, bool translating,
                              void *user_data) {
  (void)tag; (void)for_trace; (void)translating;
  uint64_t packed = (uint64_t)(ptr_uint_t)user_data;
  if (drmgr_is_first_instr(drcontext, where) && packed != 0)
    dr_insert_clean_call(drcontext, bb, where, (void *)add, false, 2,
                         OPND_CREATE_INT32((int)(uint32_t)packed),
                         OPND_CREATE_INT32((int)(uint32_t)(packed >> 32)));
  return DR_EMIT_DEFAULT;
}

static void finish(void) {
  file_t file = dr_open_file(output, DR_FILE_WRITE_REQUIRE_NEW);
  if (file == INVALID_FILE) {
    dr_fprintf(STDERR, "latency test client: cannot create result\n");
    dr_abort();
  }
  dr_fprintf(file, "{\"instructions\":%llu,\"latency_bound_x16\":%llu,\"unanalyzable\":%d}\n",
             (unsigned long long)instructions, (unsigned long long)bound_x16,
             __atomic_load_n(&unanalyzable, __ATOMIC_RELAXED));
  dr_close_file(file);
  drmgr_unregister_bb_instrumentation_event(analyze);
  drmgr_exit();
}

DR_EXPORT void dr_client_main(client_id_t id, int argc, const char *argv[]) {
  (void)id;
  if (argc != 3 || strcmp(argv[1], "-out") || strlen(argv[2]) >= sizeof(output)) {
    dr_fprintf(STDERR, "usage: -out NEW_FILE\n");
    dr_abort();
  }
  memcpy(output, argv[2], strlen(argv[2]) + 1);
  if (!drmgr_init() || !drmgr_register_bb_instrumentation_event(analyze, insert, NULL)) {
    dr_fprintf(STDERR, "latency test client: registration failed\n");
    dr_abort();
  }
  dr_register_exit_event(finish);
}

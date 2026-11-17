/* Test-only client: the production CPUID emulation plus an emulation-aware
 * basic-block counter. It needs no Durante runtime patch, so it runs on the
 * stock SDK core and checks the shared emulation code in isolation. */
#include "dr_api.h"
#include "drmgr.h"
#include "../../client/cpuid_emulation.h"

static uint64_t instructions;
static int invalid_requests;
static char output[1024];

static void invalid(void *ctx) {
  (void)ctx;
  __atomic_store_n(&invalid_requests, 1, __ATOMIC_RELAXED);
}

static dr_emit_flags_t analyze(void *drcontext, void *tag, instrlist_t *bb,
                               bool for_trace, bool translating, void **user_data) {
  (void)drcontext; (void)tag; (void)for_trace; (void)translating;
  uint count = 0;
  bool in_emulation = false;
  for (instr_t *ins = instrlist_first(bb); ins; ins = instr_get_next(ins)) {
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

static void add(int count) {
  __atomic_fetch_add(&instructions, (uint64_t)count, __ATOMIC_RELAXED);
}

static dr_emit_flags_t insert(void *drcontext, void *tag, instrlist_t *bb,
                              instr_t *where, bool for_trace, bool translating,
                              void *user_data) {
  (void)tag; (void)for_trace; (void)translating;
  if (drmgr_is_first_instr(drcontext, where) && user_data != NULL)
    dr_insert_clean_call(drcontext, bb, where, (void *)add, false, 1,
                         OPND_CREATE_INT32((int)(ptr_uint_t)user_data));
  return DR_EMIT_DEFAULT;
}

static void finish(void) {
  file_t file = dr_open_file(output, DR_FILE_WRITE_REQUIRE_NEW);
  if (file == INVALID_FILE) {
    dr_fprintf(STDERR, "cpuid test client: cannot create result\n");
    dr_abort();
  }
  dr_fprintf(file, "{\"instructions\":%llu,\"invalid_requests\":%d,\"profile\":\"%s\"}\n",
             (unsigned long long)instructions,
             __atomic_load_n(&invalid_requests, __ATOMIC_RELAXED),
             durante_cpuid_active == DURANTE_CPUID_NONE
                 ? "none" : durante_cpuid_profile_name(durante_cpuid_active));
  dr_close_file(file);
  drmgr_unregister_bb_instrumentation_event(analyze);
  if (durante_cpuid_active != DURANTE_CPUID_NONE)
    drmgr_unregister_bb_app2app_event(durante_cpuid_app2app);
  drmgr_exit();
}

DR_EXPORT void dr_client_main(client_id_t id, int argc, const char *argv[]) {
  (void)id;
  if (argc != 5 || strcmp(argv[1], "-profile") || strcmp(argv[3], "-out") ||
      strlen(argv[4]) >= sizeof(output)) {
    dr_fprintf(STDERR, "usage: -profile none|x86-64-v1|x86-64-v2 -out NEW_FILE\n");
    dr_abort();
  }
  memcpy(output, argv[4], strlen(argv[4]) + 1);
  enum durante_cpuid_profile profile = DURANTE_CPUID_NONE;
  if (strcmp(argv[2], "none")) {
    profile = durante_cpuid_profile_parse(argv[2]);
    if (profile == DURANTE_CPUID_NONE) {
      dr_fprintf(STDERR, "unknown CPUID profile\n");
      dr_abort();
    }
  }
  if (!drmgr_init()) {
    dr_fprintf(STDERR, "drmgr initialization failed\n");
    dr_abort();
  }
  if (profile != DURANTE_CPUID_NONE) {
    const char *missing = durante_cpuid_enable(profile, invalid);
    if (missing) {
      dr_fprintf(STDERR, "cpuid test client: %s\n", missing);
      dr_abort();
    }
    if (!drmgr_register_bb_app2app_event(durante_cpuid_app2app, NULL)) {
      dr_fprintf(STDERR, "app2app registration failed\n");
      dr_abort();
    }
  }
  if (!drmgr_register_bb_instrumentation_event(analyze, insert, NULL)) {
    dr_fprintf(STDERR, "instrumentation registration failed\n");
    dr_abort();
  }
  dr_register_exit_event(finish);
}

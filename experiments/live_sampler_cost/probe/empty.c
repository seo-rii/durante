/* A second client with the sampler's report lifecycle and single-thread
 * restriction. No per-block analysis, site allocation or instrumentation. */
#include "dr_api.h"
#include "drmgr.h"
#include <string.h>

static volatile int threads;
static process_id_t owner;
static file_t output;

static void fail(const char *why) {
    dr_fprintf(STDERR, "sampler_empty: %s\n", why);
    dr_abort_with_code(1);
}

static void thread_init(void *ctx) {
    (void)ctx;
    if (dr_atomic_add32_return_sum(&threads, 1) != 1)
        fail("multiple application threads");
}

static void fork_init(void *ctx) {
    (void)ctx;
    fail("multiple application processes");
}

static void finish(void) {
    if (threads != 1 || owner != dr_get_process_id())
        fail("incomplete single-thread run");
    dr_fprintf(output, "{\"schema\":1,\"arm\":\"empty\",\"pid\":%u,\"threads\":1}\n", (unsigned)owner);
    dr_close_file(output);
    drmgr_unregister_thread_init_event(thread_init);
    drmgr_exit();
}

DR_EXPORT void dr_client_main(client_id_t id, int argc, const char *argv[]) {
    (void)id;
    if (argc != 3 || strcmp(argv[1], "-out"))
        fail("expected -out <new output file>");
    output = dr_open_file(argv[2], DR_FILE_WRITE_REQUIRE_NEW | DR_FILE_CLOSE_ON_FORK);
    if (output == INVALID_FILE)
        fail("cannot create output");
    owner = dr_get_process_id();
    if (!drmgr_init() || !drmgr_register_thread_init_event(thread_init))
        fail("cannot install empty client");
    dr_register_fork_init_event(fork_init);
    dr_register_exit_event(finish);
}

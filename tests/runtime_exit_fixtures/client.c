/* Test-only client: hold one application clean call across a bounded exit. */
#include "dr_api.h"
#include <string.h>

static app_pc checkpoint;
static const char *ready_path, *end_path, *release_path;

static void marker(const char *path) {
    file_t file = dr_open_file(path, DR_FILE_WRITE_REQUIRE_NEW);
    if (file == INVALID_FILE || dr_write_file(file, "1\n", 2) != 2)
        dr_abort();
    dr_close_file(file);
}

static void bounded_pause(void) {
    marker(ready_path);
    uint64 end = dr_get_microseconds() + 4000000;
    while (dr_get_microseconds() < end) {
        if (dr_file_exists(release_path))
            return;
        dr_sleep(1);
    }
    /* A missed handshake is a fixture failure, never a normal worker return. */
    dr_abort();
}

static void loaded(void *context, const module_data_t *module, bool loaded) {
    (void)context;
    (void)loaded;
    app_pc address = (app_pc)dr_get_proc_address(module->handle, "worker_checkpoint");
    if (address != NULL)
        checkpoint = address;
}

static dr_emit_flags_t block(void *context, void *tag, instrlist_t *instructions,
                             bool for_trace, bool translating) {
    (void)for_trace;
    (void)translating;
    if (tag == checkpoint)
        dr_insert_clean_call(context, instructions, instrlist_first(instructions),
                             (void *)bounded_pause, false, 0);
    return DR_EMIT_DEFAULT;
}

static void thread_exit(void *context) { (void)context; }
static void process_exit(void) { marker(end_path); }

DR_EXPORT void dr_client_main(client_id_t id, int argc, const char *argv[]) {
    (void)id;
    if (argc != 4 || argv[1][0] != '/' || argv[2][0] != '/' || argv[3][0] != '/')
        dr_abort();
    ready_path = argv[1];
    end_path = argv[2];
    release_path = argv[3];
    dr_register_module_load_event(loaded);
    dr_register_bb_event(block);
    dr_register_thread_exit_event(thread_exit);
    dr_register_exit_event(process_exit);
}

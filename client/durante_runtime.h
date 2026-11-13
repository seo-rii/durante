/*
 * Private ABI shared with toolkit/patches/dynamorio-code-replay.patch.
 * Include dr_api.h first.  This is specific to the pinned Linux x86-64 core;
 * it is not an upstream DynamoRIO API or a hostile-host attestation.
 */
#ifndef DURANTE_RUNTIME_H
#define DURANTE_RUNTIME_H

#ifdef __cplusplus
extern "C" {
#endif

#define DR_DURANTE_RUNTIME_ABI_VERSION 1U
#define DR_DURANTE_REPLAY_RETRY 1
#define DR_DURANTE_REPLAY_AFTER_WRITE 2
#define DR_DURANTE_REPLAY_ENTRY 3
#define DR_DURANTE_REPLAY_UNKNOWN 4

typedef void (*dr_durante_code_replay_cb_t)(void *drcontext, int reason,
                                          app_pc source_pc, app_pc target_pc);

/* A strong reference also makes a client fail to load on a stock/old core.
 * The version is zero on architectures without this event's full contract.
 */
DR_API
unsigned int dr_durante_runtime_abi_version(void);

/* Register during client initialization and unregister during client exit.
 * Notifications are synchronous on the affected application thread, after the
 * core has selected a dispatch re-entry and before any re-entered client or
 * application instruction.  A cache-local retry emits no event.
 *
 * RETRY: source_pc is the translated faulting write and target_pc the dispatch
 * restart PC.  The write has not completed; a REP write may already have made
 * partial progress.  This does not mean that no application work was done.
 * AFTER_WRITE: source_pc is NULL, target_pc is the next application instruction.
 * The inline sandbox completed the write before exiting the fragment.
 * ENTRY: source_pc is NULL, target_pc is the fragment's entry.  The sandbox's
 * entry validation/threshold check exited before client/app instructions ran.
 * UNKNOWN: the core cannot supply one of these precise classifications.
 *
 * This is an informational event, not a signal/kernel-transfer notification.
 * It supplies no dr_get_mcontext()/dr_set_mcontext() context and cannot redirect
 * execution.  The callback can run in an internal signal handler: restrict it
 * to bounded per-thread bookkeeping via existing TLS and atomic operations;
 * do not allocate, take application locks, invoke code generation/flush APIs,
 * register events, perform I/O, or change application state.
 */
DR_API
void
dr_register_durante_code_replay_event(dr_durante_code_replay_cb_t callback);
DR_API
bool
dr_unregister_durante_code_replay_event(dr_durante_code_replay_cb_t callback);

#ifdef __cplusplus
}
#endif
#endif

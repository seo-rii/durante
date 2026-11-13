"""Exercise actual static client replay functions with minimal DR host stubs.

No replay implementation is copied or extracted. The temporary C translation
unit includes client/durante.c; section GC removes unrelated instrumentation.
Requires SDK headers (DURANTE_TEST_SDK or the normal repository SDK directory),
GCC and prlimit. No prepared runtime or client binary is loaded by these tests.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import tempfile
import unittest

from concurrent_spawn import cleanup_orphans, own_subreaper
from runtime_compat import run_process

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include CLIENT_SOURCE

static thread_data_t state, parent_state, child_state;
static thread_data_t *active = &state;
static int context_token;
static reg_t supplied_rcx;
static bool context_available = true;
static unsigned diagnostics;
static char record[4096];
/* Only IR representation/accessors are stubbed: the actual client's analysis
 * and suffix functions still decide how app/meta/emulation nodes are counted. */
typedef struct test_ir {
    int kind; /* 0 meta, 1 application, 2 emulation start, 3 emulation end */
    struct test_ir *next;
} test_ir;
#define CTX ((void *)&context_token)
#define PC(value) ((app_pc)(ptr_uint_t)(value))
#define START PC(0x1000)
#define WRITE PC(0x1010)
#define NEXT PC(0x1012)

instr_t *instrlist_first(instrlist_t *list) { return (instr_t *)list; }
instr_t *instr_get_next(instr_t *instruction) {
    return (instr_t *)((test_ir *)instruction)->next;
}
bool instr_is_app(instr_t *instruction) { return ((test_ir *)instruction)->kind == 1; }
bool drmgr_is_emulation_start(instr_t *instruction) { return ((test_ir *)instruction)->kind == 2; }
bool drmgr_is_emulation_end(instr_t *instruction) { return ((test_ir *)instruction)->kind == 3; }

void *dr_get_current_drcontext(void) { return CTX; }
void *drmgr_get_tls_field(void *ctx, int index) {
    (void)index;
    assert(ctx == CTX);
    return active;
}
bool dr_get_mcontext(void *ctx, dr_mcontext_t *mc) {
    assert(ctx == CTX);
    if (!context_available)
        return false;
    mc->xcx = supplied_rcx;
    return true;
}
reg_t reg_get_value(reg_id_t reg, dr_mcontext_t *mc) {
    assert(reg == DR_REG_RCX || reg == DR_REG_ECX);
    return reg == DR_REG_ECX ? (uint32_t)mc->xcx : mc->xcx;
}
ssize_t dr_fprintf(file_t file, const char *format, ...) {
    (void)file;
    (void)format;
    ++diagnostics;
    return -1;
}
void dr_abort(void) { abort(); }
int dr_snprintf(char *buffer, size_t size, const char *format, ...) {
    va_list args;
    va_start(args, format);
    int length = vsnprintf(buffer, size, format, args);
    va_end(args);
    return length;
}
ssize_t dr_write_file(file_t file, const void *buffer, size_t length) {
    assert(file == 123 && length < sizeof(record));
    memcpy(record, buffer, length);
    record[length] = 0;
    return (ssize_t)length;
}
file_t dr_open_file(const char *path, uint flags) {
    (void)path;
    (void)flags;
    /* Snapshot tests supply an already-open child descriptor. */
    abort();
}

static void reset(void) {
    memset(&state, 0, sizeof(state));
    memset(&parent_state, 0, sizeof(parent_state));
    memset(&child_state, 0, sizeof(child_state));
    active = &state;
    supplied_rcx = diagnostics = 0;
    context_available = true;
    record[0] = 0;
    total_instructions = total_signals = total_rep_bytes = total_rep_entries = 0;
    total_unsupported_rep_entries = 0;
    threads_started = 1;
    threads_ended = event_sequence = file_bytes = 0;
    accounting_error = false;
    app_exit_seen = true;
    app_exit_code = 0;
    owner_pid = 100;
    report_file = 123;
    strcpy(segment, "parent");
    __atomic_store_n(&replay_without_tls, 0, __ATOMIC_RELAXED);
}

static void snapshot(bool expected_valid) {
    snapshot_event("exec_attempt", CTX, true);
    assert(strstr(record, expected_valid ? "\"valid\":true" : "\"valid\":false"));
    assert(strstr(record, expected_valid ? "\"accounting_error\":false" :
                                         "\"accounting_error\":true"));
}

static void arm_rep(void) {
    state.instructions = 100;
    count_block(7, START);
    before_write(WRITE, NEXT, 3);
    supplied_rcx = 10;
    observe_rep(WRITE, OP_rep_movs, 1, DR_REG_RCX);
    assert(state.rep_bytes == 10 && state.rep_entries == 1);
    code_replay(CTX, DR_DURANTE_REPLAY_RETRY, WRITE, WRITE);
    assert(state.instructions == 104 && state.restart_pc == WRITE);
    assert(!state.replay_error && !state.restart_entered);
}

static void enter_rep(void) {
    count_block(3, WRITE);
    assert(state.restart_entered && state.restart_pc == WRITE);
    before_write(WRITE, NEXT, 3);
    supplied_rcx = 6;
}

int main(int argc, char **argv) {
    assert(argc == 2);
    reset();
    const char *test = argv[1];
    if (!strcmp(test, "suffix")) {
        test_ir nodes[6] = {{1, NULL}, {0, NULL}, {1, NULL},
                            {0, NULL}, {1, NULL}, {0, NULL}};
        for (unsigned i = 0; i + 1 < sizeof(nodes) / sizeof(nodes[0]); ++i)
            nodes[i].next = &nodes[i + 1];
        void *count = NULL;
        analyze_block(CTX, START, (instrlist_t *)nodes, false, false, &count);
        assert((ptr_uint_t)count == 3);
        assert(write_suffix((instrlist_t *)nodes, (instr_t *)&nodes[2]) == 2);
        /* A physical write inside a one-instruction emulation region cannot
         * borrow the count of an unrelated following application instruction. */
        nodes[0].kind = 2;
        nodes[3].kind = 3;
        analyze_block(CTX, START, (instrlist_t *)nodes, false, false, &count);
        assert((ptr_uint_t)count == 2);
        assert(write_suffix((instrlist_t *)nodes, (instr_t *)&nodes[2]) == 0);
        assert(write_suffix((instrlist_t *)nodes, (instr_t *)&nodes[4]) == 1);
    } else if (!strcmp(test, "prefix")) {
        state.instructions = 100;
        count_block(10, START);
        before_write(WRITE, NEXT, 4);
        code_replay(CTX, DR_DURANTE_REPLAY_RETRY, WRITE, WRITE);
        assert(state.instructions == 106 && state.restart_pc == WRITE);
        count_block(4, WRITE);
        assert(state.instructions == 110 && !state.restart_pc && !state.replay_error);
        assert(state.rep_bytes == 0 && state.rep_entries == 0);
        snapshot(true);
    } else if (!strcmp(test, "partial-rep-twice")) {
        arm_rep();
        code_replay(CTX, DR_DURANTE_REPLAY_ENTRY, NULL, WRITE);
        assert(state.instructions == 104 && state.restart_pc == WRITE);
        enter_rep();
        observe_rep(WRITE, OP_rep_movs, 1, DR_REG_RCX);
        assert(state.rep_bytes == 10 && state.rep_entries == 1 && !state.restart_pc);
        assert(state.write_rep_request == 10);
        code_replay(CTX, DR_DURANTE_REPLAY_RETRY, WRITE, WRITE);
        assert(state.instructions == 104 && state.restart_rep_request == 10);
        enter_rep();
        supplied_rcx = 2;
        observe_rep(WRITE, OP_rep_movs, 1, DR_REG_RCX);
        assert(state.rep_bytes == 10 && state.rep_entries == 1);
        code_replay(CTX, DR_DURANTE_REPLAY_AFTER_WRITE, NULL, NEXT);
        assert(state.instructions == 105 && !state.restart_pc && !state.replay_error);
        count_block(2, NEXT);
        assert(state.instructions == 107);
        /* A genuine later iteration at the same PC must charge a new request. */
        count_block(3, WRITE);
        before_write(WRITE, NEXT, 3);
        supplied_rcx = 4;
        observe_rep(WRITE, OP_rep_movs, 1, DR_REG_RCX);
        assert(state.rep_bytes == 14 && state.rep_entries == 2);
        snapshot(true);
    } else if (!strcmp(test, "after-write")) {
        state.instructions = 100;
        count_block(8, START);
        before_write(WRITE, NEXT, 3);
        code_replay(CTX, DR_DURANTE_REPLAY_AFTER_WRITE, NULL, NEXT);
        assert(state.instructions == 106 && !state.restart_pc && !state.write_pc);
        count_block(2, NEXT);
        assert(state.instructions == 108 && !state.replay_error);
        snapshot(true);
    } else if (!strcmp(test, "entry")) {
        state.instructions = 42;
        before_write(WRITE, NEXT, 3);
        code_replay(CTX, DR_DURANTE_REPLAY_ENTRY, NULL, START);
        assert(state.instructions == 42 && !state.write_pc && !state.replay_error);
        reset();
        arm_rep();
        code_replay(CTX, DR_DURANTE_REPLAY_ENTRY, NULL, WRITE);
        assert(state.instructions == 104 && state.restart_pc == WRITE);
        code_replay(CTX, DR_DURANTE_REPLAY_ENTRY, NULL, NEXT);
        assert(state.instructions == 104 && !state.restart_pc && state.replay_error);
        snapshot(false);
    } else if (!strncmp(test, "mismatch-", 9)) {
        arm_rep();
        if (!strcmp(test, "mismatch-block")) {
            count_block(3, NEXT);
        } else if (!strcmp(test, "mismatch-reentered")) {
            enter_rep();
            count_block(3, WRITE); /* Observer was never consumed. */
        } else {
            enter_rep();
            app_pc pc = WRITE;
            int op = OP_rep_movs, width = 1, reg = DR_REG_RCX;
            if (!strcmp(test, "mismatch-pc")) pc = NEXT;
            else if (!strcmp(test, "mismatch-opcode")) op = OP_rep_stos;
            else if (!strcmp(test, "mismatch-width")) width = 2;
            else if (!strcmp(test, "mismatch-register")) reg = DR_REG_ECX;
            else if (!strcmp(test, "mismatch-rcx")) supplied_rcx = 11;
            else assert(!"unknown mismatch test");
            observe_rep(pc, op, width, reg);
        }
        assert(state.replay_error && !state.restart_pc);
        assert(state.rep_bytes == 10 && state.rep_entries == 1);
        snapshot(false);
    } else if (!strcmp(test, "bad-checkpoints")) {
        for (int variant = 0; variant < 5; ++variant) {
            reset();
            state.instructions = 20;
            if (variant != 0)
                before_write(WRITE, NEXT, variant == 1 ? 0 : 3);
            int reason = variant == 4 ? DR_DURANTE_REPLAY_UNKNOWN : DR_DURANTE_REPLAY_RETRY;
            app_pc source = variant == 2 ? NEXT : WRITE;
            app_pc target = variant == 3 ? NEXT : WRITE;
            code_replay(CTX, reason, source, target);
            assert(state.replay_error && state.instructions == 20);
            snapshot(false);
        }
        reset();
        state.instructions = 2;
        before_write(WRITE, NEXT, 3);
        code_replay(CTX, DR_DURANTE_REPLAY_RETRY, WRITE, WRITE);
        assert(state.replay_error && state.instructions == 2);
        snapshot(false);
    } else if (!strcmp(test, "signals")) {
        dr_kernel_xfer_info_t info = {0};
        state.instructions = 20;
        before_write(WRITE, NEXT, 3);
        info.type = DR_XFER_SIGNAL_DELIVERY;
        kernel_xfer(CTX, &info);
        assert(!state.write_pc && !state.replay_error);
        before_write(WRITE, NEXT, 2); /* Handler calls the SAME PC with another suffix. */
        info.type = DR_XFER_SIGNAL_RETURN;
        kernel_xfer(CTX, &info);
        assert(!state.write_pc && !state.replay_error);
        code_replay(CTX, DR_DURANTE_REPLAY_RETRY, WRITE, WRITE);
        assert(state.replay_error && state.instructions == 20);
        reset();
        kernel_xfer(CTX, &info); /* An unrelated earlier signal is harmless. */
        count_block(5, START);
        before_write(WRITE, NEXT, 2);
        code_replay(CTX, DR_DURANTE_REPLAY_RETRY, WRITE, WRITE);
        count_block(2, WRITE);
        assert(!state.replay_error && state.instructions == 5);
        snapshot(true);
        reset();
        arm_rep();
        kernel_xfer(CTX, &info);
        assert(state.replay_error && !state.restart_pc && !state.write_pc);
        snapshot(false);
    } else if (!strcmp(test, "overflow")) {
        state.instructions = UINT64_MAX - 1;
        count_block(2, START);
        assert(state.counter_overflows && state.instructions == UINT64_MAX - 1);
        before_write(WRITE, NEXT, 1);
        code_replay(CTX, DR_DURANTE_REPLAY_RETRY, WRITE, WRITE);
        assert(state.counter_overflows && state.replay_error);
        assert(state.instructions == UINT64_MAX - 1);
        count_block(1, WRITE);
        assert(state.counter_overflows && !add_thread(&state));
        snapshot(false);
        reset();
        state.instructions = 7;
        state.rep_bytes = UINT64_MAX - 2;
        before_write(WRITE, NEXT, 3);
        supplied_rcx = 4;
        observe_rep(WRITE, OP_rep_movs, 1, DR_REG_RCX);
        assert(state.rep_error && state.rep_entries == 0);
        code_replay(CTX, DR_DURANTE_REPLAY_RETRY, WRITE, WRITE);
        count_block(3, WRITE);
        before_write(WRITE, NEXT, 3);
        supplied_rcx = 1;
        observe_rep(WRITE, OP_rep_movs, 1, DR_REG_RCX);
        assert(state.rep_error && !add_thread(&state));
        snapshot(false);
    } else if (!strcmp(test, "pending-publication")) {
        arm_rep();
        assert(!add_thread(&state));
        snapshot(false);
        state.child_pid = 200;
        state.child_file = 123;
        state.child_exit_seen = true;
        snapshot_event("end", CTX, false);
        assert(strstr(record, "\"valid\":false"));
    } else if (!strcmp(test, "vfork-local")) {
        parent_state.instructions = 77;
        child_state.instructions = 20;
        child_state.child_pid = 200;
        child_state.child_file = 123;
        child_state.child_exit_seen = true;
        active = &child_state;
        code_replay(CTX, DR_DURANTE_REPLAY_UNKNOWN, NULL, NEXT);
        assert(child_state.replay_error && !parent_state.replay_error);
        assert(!accounting_error && !add_thread(&child_state));
        snapshot_event("end", CTX, false);
        assert(strstr(record, "\"instructions\":20") && strstr(record, "\"valid\":false"));
        active = &parent_state;
        snapshot(true);
        assert(strstr(record, "\"instructions\":77") && add_thread(&parent_state));
    } else if (!strcmp(test, "missing-tls")) {
        active = NULL;
        code_replay(CTX, DR_DURANTE_REPLAY_RETRY, WRITE, WRITE);
        assert(__atomic_load_n(&replay_without_tls, __ATOMIC_RELAXED) && diagnostics == 0);
        active = &state;
        state.instructions = 10;
        snapshot(false);
        state.child_pid = 200;
        state.child_file = 123;
        state.child_exit_seen = true;
        snapshot_event("end", CTX, false);
        assert(strstr(record, "\"valid\":false"));
    } else {
        assert(!"unknown test case");
    }
    assert(diagnostics == 0);
    puts("client-replay-ok");
    return 0;
}
'''


class ClientReplayStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if platform.system() != "Linux" or platform.machine() != "x86_64":
            raise unittest.SkipTest("client replay C tests require Linux x86-64")
        configured = os.environ.get("DURANTE_TEST_SDK")
        sdk = Path(configured) if configured else ROOT / "toolkit/sdk/DynamoRIO-Linux-11.3.0-1"
        if not all((sdk / name).is_file() for name in ("include/dr_api.h", "ext/include/drmgr.h")):
            if configured:
                raise RuntimeError("configured DURANTE_TEST_SDK lacks SDK headers")
            raise unittest.SkipTest("build the SDK or set DURANTE_TEST_SDK for actual-client C tests")
        compiler, limiter = (shutil.which(name) for name in ("gcc", "prlimit"))
        if not compiler or not limiter:
            raise RuntimeError("actual-client C tests require gcc and prlimit")
        cls.limiter = str(Path(limiter).absolute())
        cls.temporary = tempfile.TemporaryDirectory(prefix="durante-client-replay-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.sources = [ROOT / "client/durante.c", ROOT / "client/durante_runtime.h"]
        cls.source_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in cls.sources}
        libc, previous = own_subreaper()
        cls.addClassCleanup(libc.prctl, 36, previous, 0, 0, 0)
        cls.addClassCleanup(cleanup_orphans)
        source = cls.directory / "client-replay.c"
        source.write_text(HARNESS)
        cls.binary = cls.directory / "client-replay"
        compile_dir = cls.directory / "compile"
        compile_dir.mkdir(mode=0o700)
        command = [compiler, "-std=gnu17", "-O2", "-Wall", "-Wextra", "-Werror",
                   "-DLINUX", "-DX86_64", "-ffunction-sections", "-fdata-sections",
                   "-Wl,--gc-sections", "-DCLIENT_SOURCE=" + json.dumps(str(cls.sources[0])),
                   "-I" + str(sdk.resolve() / "include"), "-I" + str(sdk.resolve() / "ext/include"),
                   str(source), "-o", str(cls.binary)]
        row = run_process(command, compile_dir, 30000, cls.limiter)
        if row["exit_code"] != 0 or not row["infrastructure_ok"] or row.get("stderr"):
            raise RuntimeError("actual-client harness compilation failed: " + str(row)[:12000])
        print("client_replay_source_sha256=" + json.dumps(cls.source_hashes, sort_keys=True))

    @classmethod
    def tearDownClass(cls):
        current = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in cls.sources}
        if current != cls.source_hashes:
            raise AssertionError("client sources changed during state-function validation")

    def run_case(self, name):
        directory = Path(tempfile.mkdtemp(prefix=name + "-", dir=self.directory))
        row = run_process([str(self.binary), name], directory, 5000, self.limiter)
        self.assertTrue(row["infrastructure_ok"], row)
        self.assertEqual(row["exit_code"], 0, row)
        self.assertEqual(row.get("stdout"), "client-replay-ok\n", row)
        self.assertEqual(row.get("stderr"), "", row)

    def test_executed_prefix_survives_retry(self):
        self.run_case("prefix")

    def test_suffix_ignores_meta_and_rejects_ambiguous_emulation_write(self):
        self.run_case("suffix")

    def test_partial_rep_multiple_faults_charge_original_request_once(self):
        self.run_case("partial-rep-twice")

    def test_completed_store_retains_its_instruction_charge(self):
        self.run_case("after-write")

    def test_entry_validation_does_not_change_counts(self):
        self.run_case("entry")

    def test_restart_mismatch_never_consumes_an_unrelated_rep(self):
        for kind in ("block", "reentered", "pc", "opcode", "width", "register", "rcx"):
            with self.subTest(kind=kind):
                self.run_case("mismatch-" + kind)

    def test_missing_unknown_and_underflowing_checkpoints_are_invalid(self):
        self.run_case("bad-checkpoints")

    def test_signal_return_clears_same_pc_handler_checkpoint(self):
        self.run_case("signals")

    def test_instruction_and_rep_overflow_remain_sticky(self):
        self.run_case("overflow")

    def test_pending_restart_prevents_thread_and_child_publication(self):
        self.run_case("pending-publication")

    def test_vfork_child_error_does_not_modify_parent_tls(self):
        self.run_case("vfork-local")

    def test_missing_tls_notification_never_logs_and_invalidates_snapshots(self):
        self.run_case("missing-tls")


if __name__ == "__main__":
    unittest.main()

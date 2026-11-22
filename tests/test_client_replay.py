"""Exercise actual client REP accounting and replay with minimal DR host stubs.

No replay implementation is copied or extracted. The temporary C translation
unit includes client/durante.c; section GC removes unrelated instrumentation.
Requires SDK headers (DURANTE_TEST_SDK or the normal repository SDK directory),
GCC and prlimit. The flat-entry test uses the SDK to encode and execute the
actual block instrumentation. No prepared runtime bundle is required.
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
#define DURANTE_TEST_BLOCK_LATENCY_BOUND test_latency_bound
#include CLIENT_SOURCE

static uint32_t latency_bound_result;
static uint32_t test_latency_bound(void *drcontext, void *tag, instrlist_t *bb, uint instructions) {
    (void)drcontext; (void)tag; (void)bb; (void)instructions;
    return latency_bound_result;
}

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

static reg_t syscall_result;
reg_t dr_syscall_get_result(void *ctx) {
    assert(ctx == CTX);
    return syscall_result;
}
void dr_mutex_lock(void *mutex) { assert(mutex == state_lock); }
void dr_mutex_unlock(void *mutex) { assert(mutex == state_lock); }

/* The scope fixture supplies its own ELF header and syscall arguments. */
bool dr_safe_read(const void *src, size_t size, void *dst, size_t *read) {
    memcpy(dst, src, size);
    if (read) *read = size;
    return true;
}
static reg_t syscall_args[6];
reg_t dr_syscall_get_param(void *ctx, int index) {
    assert(ctx == CTX && index >= 0 && index < 6);
    return syscall_args[index];
}

static void check_static_text_scope(void) {
    struct elf_fixture { Elf64_Ehdr header; Elf64_Phdr ph[2]; } image = {0};
    app_pc page = PC(0x401000), entry = page + 16;
    module_segment_data_t segments[] = {
        { (app_pc)&image, (app_pc)&image + sizeof(image), DR_MEMPROT_READ, 0 },
        { page, page + 4096, DR_MEMPROT_READ | DR_MEMPROT_EXEC, 4096 }
    };
    module_data_t main = { .start = segments[0].start, .entry_point = entry,
                           .num_segments = 2, .segments = segments };
    memcpy(image.header.e_ident, ELFMAG, SELFMAG);
    image.header.e_ident[EI_CLASS] = ELFCLASS64;
    image.header.e_ident[EI_DATA] = ELFDATA2LSB;
    image.header.e_machine = EM_X86_64;
    image.header.e_entry = (uintptr_t)entry;
    image.header.e_phoff = offsetof(struct elf_fixture, ph);
    image.header.e_phentsize = sizeof(Elf64_Phdr);
    image.header.e_phnum = 2;
    image.ph[0] = (Elf64_Phdr){ .p_type = PT_LOAD, .p_flags = PF_R | PF_X,
                                .p_vaddr = (uintptr_t)entry, .p_filesz = 64, .p_memsz = 64 };
    image.header.e_type = ET_DYN;
    find_static_text(&main);
    assert(!static_text_site(entry));
    image.header.e_type = ET_EXEC;
    unsigned dynamic_headers[] = { PT_INTERP, PT_DYNAMIC };
    for (size_t i = 0; i < 2; i++) {
        image.ph[1].p_type = dynamic_headers[i];
        find_static_text(&main);
        assert(!static_text_site(entry));
    }
    image.ph[1].p_type = PT_NULL;
    find_static_text(&main);
    assert(!static_text_site(page) && static_text_site(entry));
    assert(static_text_site(entry + 63) && !static_text_site(entry + 64));
    syscall_args[0] = (reg_t)page + 4096;
    syscall_args[1] = 4096;
    assert(!changes_static_text(CTX, SYS_mprotect));
    syscall_args[0] = (reg_t)page;
    assert(changes_static_text(CTX, SYS_mprotect));
    static_text_revoked = true;
    assert(!static_text_site(entry));
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
    total_rep_movs_bytes = total_rep_stos_bytes = 0;
    total_rep_movs_entries = total_rep_stos_entries = 0;
    rep_bytes_enabled = true;
    flat_inline = false;
    latency_bound_enabled = false;
    total_latency_bound_x16 = 0;
    latency_bound_result = 0;
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

static void assert_rep_counts(const thread_data_t *data, uint64_t movs_bytes,
                              uint64_t stos_bytes, uint64_t movs_entries,
                              uint64_t stos_entries) {
    assert(data->rep_movs_bytes == movs_bytes && data->rep_stos_bytes == stos_bytes);
    assert(data->rep_movs_entries == movs_entries && data->rep_stos_entries == stos_entries);
    assert(data->rep_bytes == movs_bytes + stos_bytes);
    assert(data->rep_entries == movs_entries + stos_entries);
}

static void assert_record_breakdown(uint64_t movs_bytes, uint64_t stos_bytes,
                                   uint64_t movs_entries, uint64_t stos_entries) {
    char expected[256];
    int length = snprintf(expected, sizeof(expected),
        "\"rep_movs_bytes\":%llu,\"rep_stos_bytes\":%llu,"
        "\"rep_movs_entries\":%llu,\"rep_stos_entries\":%llu,",
        (unsigned long long)movs_bytes, (unsigned long long)stos_bytes,
        (unsigned long long)movs_entries, (unsigned long long)stos_entries);
    assert(length > 0 && (size_t)length < sizeof(expected));
    assert(strstr(record, expected));
    assert(!strstr(record, "\"rep_breakdown\":"));
}

static void assert_rep_unchanged(const thread_data_t *before) {
    assert(state.rep_bytes == before->rep_bytes && state.rep_entries == before->rep_entries);
    assert(state.rep_movs_bytes == before->rep_movs_bytes);
    assert(state.rep_stos_bytes == before->rep_stos_bytes);
    assert(state.rep_movs_entries == before->rep_movs_entries);
    assert(state.rep_stos_entries == before->rep_stos_entries);
    assert(state.unsupported_rep_entries == before->unsupported_rep_entries);
}

static void arm_rep(void) {
    state.instructions = 100;
    count_block(7, START);
    before_write(WRITE, NEXT, 3);
    supplied_rcx = 10;
    observe_rep(WRITE, OP_rep_movs, 1, DR_REG_RCX);
    assert(state.rep_bytes == 10 && state.rep_entries == 1);
    assert_rep_counts(&state, 10, 0, 1, 0);
    code_replay(CTX, DR_DURANTE_REPLAY_RETRY, WRITE, WRITE);
    assert(state.instructions == 104 && state.restart_pc == WRITE);
    assert(!state.replay_error && !state.restart_entered);
}

/* Model the completed-entry latch and charge for state-callback tests. */
static void flat_enter(uint64_t count) {
    check_flat_counter(active);
    active->entry_base = active->instructions;
    active->instructions += count;
    active->write_pc = NULL;
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
    if (!strcmp(test, "static-text-scope")) {
        check_static_text_scope();
    } else if (!strcmp(test, "returning-lifecycle")) {
        state.pending_syscall = SYS_fork;
        state.pending_spawn = true;
        syscall_result = 201;
        post_syscall(CTX, SYS_fork);
        assert(strstr(record, "\"event\":\"spawn\"") && strstr(record, "\"child_pid\":201,"));
        assert(!state.pending_spawn && state.pending_syscall == -1);
    } else if (!strcmp(test, "suffix")) {
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
    } else if (!strcmp(test, "rep-breakdown-mixed")) {
        state.instructions = 1;
        supplied_rcx = 7;
        observe_rep(WRITE, OP_rep_movs, 1, DR_REG_RCX);
        supplied_rcx = 3;
        observe_rep(NEXT, OP_rep_stos, 4, DR_REG_RCX);
        supplied_rcx = 0;
        observe_rep(WRITE, OP_rep_movs, 8, DR_REG_RCX);
        observe_rep(NEXT, OP_rep_stos, 2, DR_REG_ECX);
        assert_rep_counts(&state, 7, 12, 2, 2);
        const int unsupported[] = {OP_rep_cmps, OP_repne_cmps, OP_rep_scas,
            OP_repne_scas, OP_rep_lods, OP_rep_ins, OP_rep_outs};
        context_available = false; /* Unsupported forms never need RCX. */
        for (unsigned i = 0; i < sizeof(unsupported) / sizeof(unsupported[0]); ++i)
            observe_rep(WRITE, unsupported[i], 0, DR_REG_NULL);
        assert(!state.rep_error && state.unsupported_rep_entries == 7);
        assert_rep_counts(&state, 7, 12, 2, 2);
        snapshot(true);
        assert_record_breakdown(7, 12, 2, 2);
    } else if (!strcmp(test, "rep-breakdown-widths")) {
        const int opcodes[] = {OP_rep_movs, OP_rep_stos};
        const int widths[] = {1, 2, 4, 8};
        const int registers[] = {DR_REG_RCX, DR_REG_ECX};
        for (unsigned op = 0; op < 2; ++op) {
            for (unsigned width = 0; width < 4; ++width) {
                for (unsigned reg = 0; reg < 2; ++reg) {
                    reset();
                    state.instructions = 1;
                    supplied_rcx = UINT64_C(0x100000005);
                    observe_rep(WRITE, opcodes[op], widths[width], registers[reg]);
                    uint64_t bytes = (reg ? 5 : UINT64_C(0x100000005)) * widths[width];
                    assert(!state.rep_error);
                    assert_rep_counts(&state, op ? 0 : bytes, op ? bytes : 0,
                                      op ? 0 : 1, op ? 1 : 0);
                    snapshot(true);
                    assert_record_breakdown(op ? 0 : bytes, op ? bytes : 0,
                                            op ? 0 : 1, op ? 1 : 0);
                }
            }
        }
    } else if (!strcmp(test, "rep-breakdown-disabled")) {
        rep_bytes_enabled = false;
        char parent_segment[80];
        memset(parent_segment, 'p', sizeof(parent_segment) - 1);
        parent_segment[sizeof(parent_segment) - 1] = 0;
        start_record(NULL, "exec", 999, parent_segment);
        assert(strstr(record, "\"rep_breakdown_version\":1"));
        assert(strstr(record, "\"rep_bytes_enabled\":false"));
        assert(strstr(record, "\"sdk_version\":\"11.3.0\""));
        state.instructions = 1;
        snapshot(true);
        assert_record_breakdown(0, 0, 0, 0);
        assert(add_thread(&state));
        threads_ended = 1;
        snapshot_event("end", NULL, false);
        assert(strstr(record, "\"valid\":true"));
        assert_record_breakdown(0, 0, 0, 0);
    } else if (!strcmp(test, "rep-breakdown-overflow")) {
        const int opcodes[] = {OP_rep_movs, OP_rep_stos};
        for (unsigned op = 0; op < 2; ++op) {
            for (unsigned variant = 0; variant < 5; ++variant) {
                reset();
                state.instructions = 7;
                supplied_rcx = 5;
                observe_rep(WRITE, OP_rep_movs, 1, DR_REG_RCX);
                supplied_rcx = 7;
                observe_rep(NEXT, OP_rep_stos, 1, DR_REG_RCX);
                uint64_t *kind_bytes = op ? &state.rep_stos_bytes : &state.rep_movs_bytes;
                uint64_t *kind_entries = op ? &state.rep_stos_entries : &state.rep_movs_entries;
                if (variant == 0) state.rep_bytes = UINT64_MAX - 1;
                if (variant == 1) state.rep_entries = UINT64_MAX;
                if (variant == 2) *kind_bytes = UINT64_MAX - 1;
                if (variant == 3) *kind_entries = UINT64_MAX;
                thread_data_t before = state;
                supplied_rcx = variant == 4 ? UINT64_MAX : 2;
                observe_rep(WRITE, opcodes[op], variant == 4 ? 8 : 1, DR_REG_RCX);
                assert(state.rep_error && !add_thread(&state));
                assert_rep_unchanged(&before);
                snapshot(false);
            }
            reset();
            state.instructions = 7;
            supplied_rcx = UINT64_MAX;
            observe_rep(WRITE, opcodes[op], 1, DR_REG_RCX);
            supplied_rcx = 0;
            observe_rep(WRITE, opcodes[op], 1, DR_REG_RCX);
            assert(!state.rep_error);
            assert_rep_counts(&state, op ? 0 : UINT64_MAX, op ? UINT64_MAX : 0,
                              op ? 0 : 2, op ? 2 : 0);
            snapshot(true);
            thread_data_t before = state;
            supplied_rcx = 1;
            observe_rep(WRITE, opcodes[op], 1, DR_REG_RCX);
            assert(state.rep_error);
            assert_rep_unchanged(&before);
        }
    } else if (!strcmp(test, "rep-breakdown-aggregation")) {
        state.instructions = 5;
        supplied_rcx = 5;
        observe_rep(WRITE, OP_rep_movs, 2, DR_REG_RCX);
        supplied_rcx = 3;
        observe_rep(NEXT, OP_rep_stos, 4, DR_REG_RCX);
        assert(add_thread(&state));
        threads_started = 2;
        threads_ended = 1;
        active = &parent_state;
        parent_state.instructions = 9;
        supplied_rcx = 0;
        observe_rep(WRITE, OP_rep_movs, 1, DR_REG_RCX);
        supplied_rcx = 2;
        observe_rep(NEXT, OP_rep_stos, 8, DR_REG_RCX);
        for (unsigned i = 0; i < 2; ++i) {
            snapshot(true);
            assert_record_breakdown(10, 28, 2, 2);
            assert(total_rep_bytes == 22 && total_rep_entries == 2);
            assert(total_rep_movs_bytes == 10 && total_rep_stos_bytes == 12);
            assert(total_rep_movs_entries == 1 && total_rep_stos_entries == 1);
        }
        assert(add_thread(&parent_state));
        threads_ended = 2;
        snapshot_event("end", NULL, false);
        assert(strstr(record, "\"valid\":true"));
        assert_record_breakdown(10, 28, 2, 2);
        assert(total_rep_bytes == 38 && total_rep_entries == 4);
    } else if (!strcmp(test, "rep-breakdown-aggregate-overflow")) {
        for (unsigned variant = 0; variant < 6; ++variant) {
            reset();
            state.instructions = 5;
            supplied_rcx = 2;
            observe_rep(WRITE, OP_rep_movs, 4, DR_REG_RCX);
            supplied_rcx = 3;
            observe_rep(NEXT, OP_rep_stos, 2, DR_REG_RCX);
            total_instructions = 10;
            total_rep_bytes = 30;
            total_rep_entries = 3;
            total_rep_movs_bytes = 10;
            total_rep_stos_bytes = 20;
            total_rep_movs_entries = 1;
            total_rep_stos_entries = 2;
            uint64_t *fields[] = {&total_rep_bytes, &total_rep_entries,
                &total_rep_movs_bytes, &total_rep_stos_bytes,
                &total_rep_movs_entries, &total_rep_stos_entries};
            *fields[variant] = UINT64_MAX;
            uint64_t before[6];
            for (unsigned i = 0; i < 6; ++i) before[i] = *fields[i];
            assert(!add_thread(&state));
            assert(total_instructions == 10);
            for (unsigned i = 0; i < 6; ++i) assert(*fields[i] == before[i]);
            snapshot(false);
            assert_record_breakdown(before[2], before[3], before[4], before[5]);
            assert(total_instructions == 10);
            for (unsigned i = 0; i < 6; ++i) assert(*fields[i] == before[i]);
            assert_rep_counts(&state, 8, 6, 1, 1);
        }
    } else if (!strcmp(test, "rep-breakdown-replay-kinds")) {
        const int opcodes[] = {OP_rep_movs, OP_rep_stos};
        for (unsigned op = 0; op < 2; ++op) {
            reset();
            supplied_rcx = 3;
            observe_rep(NEXT, opcodes[1 - op], 2, DR_REG_RCX);
            state.instructions = 100;
            count_block(7, START);
            before_write(WRITE, NEXT, 3);
            supplied_rcx = 10;
            observe_rep(WRITE, opcodes[op], 4, DR_REG_RCX);
            code_replay(CTX, DR_DURANTE_REPLAY_RETRY, WRITE, WRITE);
            const unsigned remaining[] = {6, 2, 0};
            for (unsigned retry = 0; retry < 3; ++retry) {
                code_replay(CTX, DR_DURANTE_REPLAY_ENTRY, NULL, WRITE);
                count_block(3, WRITE);
                before_write(WRITE, NEXT, 3);
                supplied_rcx = remaining[retry];
                observe_rep(WRITE, opcodes[op], 4, DR_REG_RCX);
                assert(state.write_rep_request == 10 && !state.restart_pc);
                assert_rep_counts(&state, op ? 6 : 40, op ? 40 : 6, 1, 1);
                code_replay(CTX, retry == 2 ? DR_DURANTE_REPLAY_AFTER_WRITE :
                            DR_DURANTE_REPLAY_RETRY, retry == 2 ? NULL : WRITE,
                            retry == 2 ? NEXT : WRITE);
            }
            count_block(2, NEXT);
            count_block(3, WRITE);
            before_write(WRITE, NEXT, 3);
            supplied_rcx = 4;
            observe_rep(WRITE, opcodes[op], 4, DR_REG_RCX);
            assert(!state.rep_error && !state.replay_error);
            assert_rep_counts(&state, op ? 6 : 56, op ? 56 : 6,
                              op ? 1 : 2, op ? 2 : 1);
            snapshot(true);
            assert_record_breakdown(op ? 6 : 56, op ? 56 : 6,
                                    op ? 1 : 2, op ? 2 : 1);
        }
    } else if (!strcmp(test, "rep-breakdown-vfork")) {
        total_instructions = 50;
        total_rep_bytes = total_rep_movs_bytes = 900;
        total_rep_entries = total_rep_movs_entries = 9;
        threads_started = 2;
        threads_ended = 1;
        active = &parent_state;
        parent_state.instructions = 77;
        supplied_rcx = 7;
        observe_rep(WRITE, OP_rep_movs, 1, DR_REG_RCX);
        active = &child_state;
        child_state.instructions = 20;
        child_state.child_pid = 200;
        child_state.child_file = 123;
        child_state.child_exit_seen = true;
        strcpy(child_state.child_segment, "child");
        start_record(&child_state, "fork", owner_pid, segment);
        assert(strstr(record, "\"rep_breakdown_version\":1"));
        assert(strstr(record, "\"rep_bytes_enabled\":true"));
        supplied_rcx = 4;
        observe_rep(WRITE, OP_rep_stos, 8, DR_REG_RCX);
        supplied_rcx = 0;
        observe_rep(NEXT, OP_rep_movs, 1, DR_REG_RCX);
        snapshot_event("end", CTX, false);
        assert(strstr(record, "\"pid\":200") && strstr(record, "\"valid\":true"));
        assert_record_breakdown(0, 32, 1, 1);
        assert(total_rep_bytes == 900 && total_rep_entries == 9);
        assert_rep_counts(&parent_state, 7, 0, 1, 0);
        active = &parent_state;
        snapshot(true);
        assert_record_breakdown(907, 0, 10, 0);
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
        assert_rep_counts(&state, 14, 0, 2, 0);
        snapshot(true);
        assert_record_breakdown(14, 0, 2, 0);
    } else if (!strcmp(test, "latency-pack")) {
        test_ir nodes[3] = {{1, NULL}, {0, NULL}, {1, NULL}};
        nodes[0].next = &nodes[1];
        nodes[1].next = &nodes[2];
        void *packed = NULL;
        latency_bound_result = 77; /* Ignored while the option is off. */
        analyze_block(CTX, START, (instrlist_t *)nodes, false, false, &packed);
        assert((ptr_uint_t)packed == 2);
        latency_bound_enabled = true;
        analyze_block(CTX, START, (instrlist_t *)nodes, false, false, &packed);
        assert((ptr_uint_t)packed == (2 | (UINT64_C(77) << 32)));
        latency_bound_result = UINT32_MAX;
        analyze_block(CTX, START, (instrlist_t *)nodes, false, false, &packed);
        assert((uint32_t)(ptr_uint_t)packed == 2 && (ptr_uint_t)packed >> 32 == UINT32_MAX);
    } else if (!strcmp(test, "latency-records")) {
        start_record(NULL, "initial", 0, "");
        assert(!strstr(record, "latency_bound"));
        state.instructions = 1;
        snapshot(true);
        assert(!strstr(record, "latency_bound"));
        latency_bound_enabled = true;
        start_record(NULL, "initial", 0, "");
        assert(strstr(record, "\"latency_bound_version\":1"));
        count_block_latency(4, START, 16);
        assert(state.instructions == 5 && state.latency_bound_x16 == 16);
        snapshot(true);
        assert(strstr(record, "\"latency_bound_x16\":16"));
        assert(add_thread(&state) && total_latency_bound_x16 == 16);
        threads_ended = 1;
        snapshot_event("end", NULL, false);
        assert(strstr(record, "\"valid\":true") && strstr(record, "\"latency_bound_x16\":16"));
    } else if (!strcmp(test, "latency-replay")) {
        latency_bound_enabled = true;
        /* A retried write returns its whole unexecuted suffix: 3 of 7. */
        state.instructions = 100;
        state.latency_bound_x16 = 1000;
        count_block_latency(7, START, 70);
        before_write(WRITE, NEXT, 3);
        code_replay(CTX, DR_DURANTE_REPLAY_RETRY, WRITE, WRITE);
        assert(state.instructions == 104 && state.latency_bound_x16 == 1040);
        count_block_latency(3, WRITE, 13);
        assert(state.instructions == 107 && state.latency_bound_x16 == 1053);
        /* A completed write keeps its own charge: 2 of 3, rounded down. */
        before_write(WRITE, NEXT, 3);
        code_replay(CTX, DR_DURANTE_REPLAY_AFTER_WRITE, NULL, NEXT);
        assert(state.instructions == 105 && state.latency_bound_x16 == 1045);
        assert(!state.replay_error && !state.latency_error);
        snapshot(true);
        assert(strstr(record, "\"latency_bound_x16\":1045"));
    } else if (!strcmp(test, "operand-bytes-replay")) {
        latency_bound_enabled = memory_operand_bytes_enabled = true;
        /* The prefix charges 4 + 0 + 32 bytes. The remaining three
         * instructions charge a 1-byte store, a 16-byte load and a 2-byte load. */
        count_block_memory(6, START, 24, 55);
        before_write_memory(WRITE, NEXT, 3, 19, 1);
        code_replay(CTX, DR_DURANTE_REPLAY_RETRY, WRITE, WRITE);
        assert(state.instructions == 3 && state.memory_operand_bytes == 36);
        count_block_memory(3, WRITE, 12, 19);
        before_write_memory(WRITE, NEXT, 3, 19, 1);
        code_replay(CTX, DR_DURANTE_REPLAY_AFTER_WRITE, NULL, NEXT);
        assert(state.instructions == 4 && state.memory_operand_bytes == 37);
        count_block_memory(2, NEXT, 8, 18);
        assert(state.instructions == 6 && state.memory_operand_bytes == 55);
        snapshot(true);
    } else if (!strcmp(test, "latency-invalid")) {
        latency_bound_enabled = true;
        state.instructions = 1;
        count_block_latency(2, START, -1); /* UINT32_MAX: unanalyzable block. */
        assert(state.latency_error && state.instructions == 3);
        snapshot(false);
        reset();
        latency_bound_enabled = true;
        state.instructions = 1;
        state.latency_bound_x16 = UINT64_MAX - 1;
        count_block_latency(1, START, 2);
        assert(state.counter_overflows && state.latency_bound_x16 == UINT64_MAX - 1);
        snapshot(false);
        reset();
        latency_bound_enabled = true;
        /* A share larger than the charged bound cannot be returned. */
        state.instructions = 10;
        state.write_pc = WRITE;
        state.write_next_pc = NEXT;
        state.write_suffix = 2;
        state.write_block_instructions = 2;
        state.write_block_bound_x16 = 50;
        state.latency_bound_x16 = 10;
        code_replay(CTX, DR_DURANTE_REPLAY_RETRY, WRITE, WRITE);
        assert(state.latency_error && state.latency_bound_x16 == 10);
        snapshot(false);
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
        assert_rep_counts(&state, 10, 0, 1, 0);
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
    } else if (!strcmp(test, "live-replay")) {
        live_work_t page = {0};
        live_work = &page;
        count_block(4096, START);
        assert(page.instructions == 0);
        before_write(WRITE, NEXT, 6);
        code_replay(CTX, DR_DURANTE_REPLAY_RETRY, WRITE, WRITE);
        count_block(6, WRITE);
        assert(page.instructions == 0);
        count_block(3, NEXT);
        assert(page.instructions == 4096);
        publish_live_work(&state, true);
        publish_live_work(&state, true);
        assert(page.instructions == 4099 && page.invalid == 0);
        live_work = NULL;
    } else if (!strcmp(test, "live-saturation")) {
        live_work_t page = {.instructions = UINT64_MAX - 1, .movs = UINT64_MAX};
        live_work = &page;
        state.instructions = state.rep_movs_bytes = 1;
        state.rep_stos_bytes = UINT64_MAX;
        publish_live_work(&state, true);
        assert(page.instructions == UINT64_MAX && page.movs == UINT64_MAX &&
               page.stos == UINT64_MAX && page.overflow == 2 && !page.invalid);
        state.instructions = 2;
        publish_live_work(&state, true);
        assert(page.instructions == UINT64_MAX && page.overflow == 3);
        live_work = NULL;
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
    } else if (!strcmp(test, "flat-prefix")) {
        flat_inline = true;
        state.instructions = 100;
        flat_enter(10);
        before_write(WRITE, NEXT, 4);
        code_replay(CTX, DR_DURANTE_REPLAY_RETRY, WRITE, WRITE);
        assert(state.instructions == 106 && state.restart_pc == WRITE);
        assert(state.restart_base == 106);
        /* A signal before the resumed entry still invalidates the retry. */
        thread_data_t pending = state;
        flat_enter(4);
        kernel_xfer(CTX, NULL);
        assert(state.instructions == 110 && !state.restart_pc && !state.replay_error);
        snapshot(true);
        state = pending;
        kernel_xfer(CTX, NULL);
        assert(state.replay_error && !state.restart_pc);
        snapshot(false);
    } else if (!strcmp(test, "flat-first-write")) {
        /* A fault at the first instruction returns the whole entry charge. */
        flat_inline = true;
        state.instructions = 100;
        flat_enter(4);
        before_write(START, WRITE, 4);
        code_replay(CTX, DR_DURANTE_REPLAY_RETRY, START, START);
        assert(state.instructions == 100 && state.entry_base == 100);
        assert(state.restart_base == 100 && state.restart_pc == START);
        snapshot(false); /* Still pending: no block has been entered. */
        flat_enter(4);
        snapshot(true);
        assert(!state.restart_pc && state.instructions == 104);
    } else if (!strcmp(test, "flat-rep")) {
        flat_inline = true;
        state.instructions = 100;
        flat_enter(7);
        before_write(WRITE, NEXT, 3);
        supplied_rcx = 10;
        observe_rep(WRITE, OP_rep_movs, 1, DR_REG_RCX);
        code_replay(CTX, DR_DURANTE_REPLAY_RETRY, WRITE, WRITE);
        assert(state.instructions == 104 && state.restart_pc == WRITE);
        code_replay(CTX, DR_DURANTE_REPLAY_ENTRY, NULL, WRITE);
        assert(state.restart_pc == WRITE && !state.replay_error);
        flat_enter(3);
        before_write(WRITE, NEXT, 3);
        supplied_rcx = 6;
        observe_rep(WRITE, OP_rep_movs, 1, DR_REG_RCX);
        assert(state.rep_bytes == 10 && state.rep_entries == 1 && !state.restart_pc);
        assert(state.write_rep_request == 10);
        code_replay(CTX, DR_DURANTE_REPLAY_RETRY, WRITE, WRITE);
        assert(state.instructions == 104 && state.restart_rep_request == 10);
        flat_enter(3);
        before_write(WRITE, NEXT, 3);
        supplied_rcx = 2;
        observe_rep(WRITE, OP_rep_movs, 1, DR_REG_RCX);
        assert(state.rep_bytes == 10 && state.rep_entries == 1);
        code_replay(CTX, DR_DURANTE_REPLAY_AFTER_WRITE, NULL, NEXT);
        assert(state.instructions == 105 && !state.restart_pc && !state.replay_error);
        flat_enter(2);
        flat_enter(3);
        before_write(WRITE, NEXT, 3);
        supplied_rcx = 4;
        observe_rep(WRITE, OP_rep_movs, 1, DR_REG_RCX);
        assert(state.instructions == 110 && state.rep_bytes == 14 && state.rep_entries == 2);
        assert_rep_counts(&state, 14, 0, 2, 0);
        snapshot(true);
        assert_record_breakdown(14, 0, 2, 0);
    } else if (!strcmp(test, "flat-rep-missed")) {
        flat_inline = true;
        state.instructions = 100;
        flat_enter(7);
        before_write(WRITE, NEXT, 3);
        supplied_rcx = 10;
        observe_rep(WRITE, OP_rep_movs, 1, DR_REG_RCX);
        code_replay(CTX, DR_DURANTE_REPLAY_RETRY, WRITE, WRITE);
        /* The resumed REP block must observe its REP before another entry. */
        flat_enter(3);
        flat_enter(2);
        supplied_rcx = 6;
        observe_rep(WRITE, OP_rep_movs, 1, DR_REG_RCX);
        assert(state.replay_error && state.rep_bytes == 16 && state.rep_entries == 2);
        assert(!add_thread(&state));
        snapshot(false);
    } else if (!strcmp(test, "flat-overflow")) {
        flat_inline = true;
        state.instructions = UINT64_MAX - 1;
        flat_enter(5);
        assert(state.instructions == 3);
        flat_enter(2);
        assert(state.instructions == 5 && state.entry_base == 3);
        snapshot(false);
        assert(state.counter_overflows && !add_thread(&state));
        reset();
        flat_inline = true;
        state.instructions = UINT64_MAX - 5;
        flat_enter(5);
        snapshot(true);
        assert(add_thread(&state) && total_instructions == UINT64_MAX);
        reset();
        flat_inline = true;
        state.instructions = UINT64_MAX - 1;
        flat_enter(5);
        before_write(START, NEXT, 2);
        code_replay(CTX, DR_DURANTE_REPLAY_RETRY, START, START);
        assert(state.counter_overflows && state.replay_error && !state.restart_pc);
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


FLAT_ENTRY_HARNESS = r'''
#include <asm/prctl.h>
#include <assert.h>
#include <stdio.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>
#include CLIENT_SOURCE

int main(void) {
    void *ctx = dr_standalone_init();
    raw_tls_register = DR_SEG_GS;
    const size_t page = (size_t)sysconf(_SC_PAGESIZE);
    byte *code = mmap(NULL, page, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    assert(code != MAP_FAILED);
    instrlist_t *bb = instrlist_create(ctx);
    instr_t *ret = INSTR_CREATE_ret(ctx);
    instrlist_append(bb, ret);
    insert_flat_charge(ctx, bb, ret, 5);
    for (instr_t *ins = instrlist_first(bb); ins != ret; ins = instr_get_next(ins))
        assert(!(instr_get_arith_flags(ins, DR_QUERY_DEFAULT) & (EFLAGS_READ_6 | EFLAGS_WRITE_6)));
    byte *end = instrlist_encode(ctx, bb, code, true);
    assert(end && end <= code + page);
    instrlist_clear_and_destroy(ctx, bb);
    dr_standalone_exit();
    assert(mprotect(code, page, PROT_READ | PROT_EXEC) == 0);

    /* The emitted sequence uses GS-relative raw TLS on x86-64 Linux. Execute
     * it against an isolated slot array, with the same production layout. */
    reg_t original_gs, slots[RAW_TLS_SLOTS] = {0};
    assert(syscall(SYS_arch_prctl, ARCH_GET_GS, &original_gs) == 0);
    assert(syscall(SYS_arch_prctl, ARCH_SET_GS, slots) == 0);
    const uint64_t starts[] = {0, (UINT64_C(1) << 56) - 4, (UINT64_C(1) << 63) - 4,
                               UINT64_MAX - 15, UINT64_MAX - 10, UINT64_MAX - 9,
                               UINT64_MAX - 1, UINT64_MAX};
    for (size_t i = 0; i < sizeof(starts) / sizeof(starts[0]); ++i) {
        thread_data_t d = {.instructions = starts[i]};
        slots[RAW_TLS_STATE] = (reg_t)&d;
        for (int j = 0; j < 3; ++j) {
            uint64_t before = d.instructions;
            ((void (*)(void))code)();
            assert(d.entry_base == before && d.instructions == before + 5);
            /* The next entry latches the preceding charge before replacing its base. */
            assert(d.counter_overflows == (before < starts[i]));
        }
    }
    assert(syscall(SYS_arch_prctl, ARCH_SET_GS, original_gs) == 0);
    assert(munmap(code, page) == 0);
    puts("flat-charge-ok");
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
        cls.sdk_lib = sdk.resolve() / "lib64/release"
        cls.compile_flags = [
            compiler, "-std=gnu17", "-O2", "-Wall", "-Wextra", "-Werror", "-DLINUX", "-DX86_64",
            "-ffunction-sections", "-fdata-sections", "-Wl,--gc-sections",
            "-DCLIENT_SOURCE=" + json.dumps(str(cls.sources[0])),
            "-I" + str(sdk.resolve() / "include"), "-I" + str(sdk.resolve() / "ext/include"),
        ]
        command = [*cls.compile_flags, str(source), "-o", str(cls.binary)]
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

    def test_returning_lifecycle_syscall_clears_pending_number(self):
        self.run_case("returning-lifecycle")

    def test_static_text_scope(self):
        self.run_case("static-text-scope")

    def test_suffix_ignores_meta_and_rejects_ambiguous_emulation_write(self):
        self.run_case("suffix")

    def test_rep_breakdown_mixes_kinds_zero_repeats_and_unsupported_forms(self):
        self.run_case("rep-breakdown-mixed")

    def test_rep_breakdown_uses_each_width_and_architectural_count_register(self):
        self.run_case("rep-breakdown-widths")

    def test_disabled_rep_still_announces_version_and_zero_breakdown(self):
        self.run_case("rep-breakdown-disabled")

    def test_rep_breakdown_overflow_never_partially_updates_counters(self):
        self.run_case("rep-breakdown-overflow")

    def test_rep_breakdown_aggregates_threads_and_preserves_exec_snapshots(self):
        self.run_case("rep-breakdown-aggregation")

    def test_rep_breakdown_aggregate_overflow_invalidates_without_mutation(self):
        self.run_case("rep-breakdown-aggregate-overflow")

    def test_rep_replay_preserves_original_kind_and_charge_across_retries(self):
        self.run_case("rep-breakdown-replay-kinds")

    def test_shared_vm_child_publishes_only_its_own_rep_breakdown(self):
        self.run_case("rep-breakdown-vfork")

    def test_partial_rep_multiple_faults_charge_original_request_once(self):
        self.run_case("partial-rep-twice")

    def test_latency_bound_is_packed_only_when_enabled(self):
        self.run_case("latency-pack")

    def test_latency_bound_capability_and_counter_reach_records(self):
        self.run_case("latency-records")

    def test_latency_bound_replay_returns_proportional_share(self):
        self.run_case("latency-replay")

    def test_operand_bytes_replay_keeps_only_the_completed_prefix(self):
        self.run_case("operand-bytes-replay")

    def test_unanalyzable_overflowing_or_underflowing_latency_bound_is_invalid(self):
        self.run_case("latency-invalid")

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

    def test_live_progress_publishes_prior_corrected_block_once(self):
        self.run_case("live-replay")

    def test_live_components_saturate_independently_without_wrapping(self):
        self.run_case("live-saturation")

    def test_instruction_and_rep_overflow_remain_sticky(self):
        self.run_case("overflow")

    def test_pending_restart_prevents_thread_and_child_publication(self):
        self.run_case("pending-publication")

    def test_vfork_child_error_does_not_modify_parent_tls(self):
        self.run_case("vfork-local")

    def test_emitted_flat_entry_latches_wrap_and_preserves_flags(self):
        if not (self.sdk_lib / "libdynamorio.so").is_file():
            self.skipTest("encoding flat instrumentation requires the SDK standalone library")
        build = self.directory / "flat-entry"
        build.mkdir()
        source, binary = build / "emit.c", build / "emit"
        source.write_text(FLAT_ENTRY_HARNESS)
        command = [*self.compile_flags, str(source), "-L" + str(self.sdk_lib),
                   "-Wl,-rpath," + str(self.sdk_lib), "-ldynamorio", "-o", str(binary)]
        row = run_process(command, build, 30000, self.limiter)
        self.assertTrue(row["infrastructure_ok"], row)
        self.assertEqual(row["exit_code"], 0, row)
        # Bound the standalone encoder's default virtual-address reservation.
        row = run_process(["env", "DYNAMORIO_OPTIONS=-vm_size 32M -vmheap_size 32M",
                           str(binary)], build, 5000, self.limiter)
        self.assertTrue(row["infrastructure_ok"], row)
        self.assertEqual(row["exit_code"], 0, row)
        self.assertEqual(row["stdout"], "flat-charge-ok\n")

    def test_flat_entry_settles_non_rep_retry_and_rejects_pending_signal(self):
        self.run_case("flat-prefix")

    def test_flat_first_instruction_retry_waits_for_resumed_entry(self):
        self.run_case("flat-first-write")

    def test_flat_rep_retries_charge_original_request_once(self):
        self.run_case("flat-rep")

    def test_flat_rep_restart_must_observe_in_first_resumed_block(self):
        self.run_case("flat-rep-missed")

    def test_flat_counter_wrap_is_sticky_invalidity(self):
        self.run_case("flat-overflow")

    def test_missing_tls_notification_never_logs_and_invalidates_snapshots(self):
        self.run_case("missing-tls")


if __name__ == "__main__":
    unittest.main()

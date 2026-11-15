/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Diagnostic guest work, composed with an independent execution policy.
 * Never instrument the host emulator with the native DynamoRIO client. */
#define _POSIX_C_SOURCE 200809L
#include "plugin-api.h"
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#if !defined(__linux__) || !defined(__x86_64__)
#error "durante-qemu supports Linux x86-64 hosts only"
#endif

QEMU_PLUGIN_EXPORT int qemu_plugin_version = QEMU_PLUGIN_VERSION;
enum failure { NONE, MULTIPLE_CPUS, PROCESS_CHANGE, OVERFLOW, EMPTY, NO_EXIT };
static const char *const reasons[] = {
    "", "multiple guest threads", "guest process creation or exec attempted",
    "instruction counter overflow", "no guest instructions", "no normal guest exit",
};
static _Atomic uint64_t instructions;
static _Atomic unsigned int cpus;
static _Atomic int failed;
/* Only CPU 0 sets exit state. Keep it atomic even when an unsupported second
 * CPU makes QEMU enter its final callback from another thread. */
static _Atomic bool exit_pending;
static bool installed;
static _Atomic bool finalized;
static _Atomic unsigned int exit_code;
static pid_t owner;
static int report_fd = -1;

static void invalidate(enum failure reason)
{
    int expected = NONE;
    (void)atomic_compare_exchange_strong(&failed, &expected, reason);
}

static void cpu_started(qemu_plugin_id_t id, unsigned int cpu)
{
    (void)id;
    if (atomic_fetch_add(&cpus, 1) != 0 || cpu != 0) {
        invalidate(MULTIPLE_CPUS);
    }
}

static void instruction(unsigned int cpu, void *data)
{
    (void)data;
    if (cpu != 0) {
        invalidate(MULTIPLE_CPUS);
        return;
    }
    if (atomic_fetch_add_explicit(&instructions, 1, memory_order_relaxed) == UINT64_MAX) {
        invalidate(OVERFLOW);
    }
}

static void translated(qemu_plugin_id_t id, struct qemu_plugin_tb *tb)
{
    (void)id;
    for (size_t i = 0; i < qemu_plugin_tb_n_insns(tb); ++i) {
        qemu_plugin_register_vcpu_insn_exec_cb(qemu_plugin_tb_get_insn(tb, i),
            instruction, QEMU_PLUGIN_CB_NO_REGS, NULL);
    }
}

static void syscall_enter(qemu_plugin_id_t id, unsigned int cpu, int64_t number,
    uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4, uint64_t a5, uint64_t a6,
    uint64_t a7, uint64_t a8)
{
    (void)id; (void)a2; (void)a3; (void)a4; (void)a5; (void)a6; (void)a7; (void)a8;
    if (cpu != 0) {
        invalidate(MULTIPLE_CPUS);
        return;
    }
    /* Conservatively invalidate even failed creation/exec attempts. This is
     * a single-process metric, not an incomplete native process-tree total. */
    if (number == 56 || number == 57 || number == 58 || number == 59 ||
        number == 322 || number == 435 || number < 0 || number >= 0x40000000) {
        invalidate(PROCESS_CHANGE);
    }
    if (number == 60 || number == 231) {
        exit_pending = true;
        exit_code = a1 & 255;
    }
}

static void syscall_return(qemu_plugin_id_t id, unsigned int cpu,
    int64_t number, int64_t result)
{
    (void)id; (void)result;
    /* QEMU can restart exit when a signal is pending. It did not complete;
     * a later signal crash must not look like the earlier requested exit. */
    if (cpu == 0 && (number == 60 || number == 231)) {
        exit_pending = false;
    }
}

static void finished(qemu_plugin_id_t id, void *data)
{
    (void)id; (void)data;
    /* A child must never overwrite its parent's inherited report descriptor. */
    if (getpid() != owner || atomic_exchange(&finalized, true)) {
        return;
    }
    if (atomic_load(&cpus) != 1) {
        invalidate(MULTIPLE_CPUS);
    }
    uint64_t count = atomic_load(&instructions);
    if (!count) {
        invalidate(EMPTY);
    }
    if (!exit_pending) {
        invalidate(NO_EXIT);
    }
    char report[512];
    int failure = atomic_load(&failed);
    int length;
    if (failure == NONE) {
        length = snprintf(report, sizeof(report),
            "{\"schema\":1,\"kind\":\"durante-qemu-work\",\"pid\":%ld,"
            "\"complete\":true,\"instructions\":%" PRIu64 ",\"exit_code\":%u}\n",
            (long)owner, count, exit_code);
    } else {
        length = snprintf(report, sizeof(report),
            "{\"schema\":1,\"kind\":\"durante-qemu-work\",\"pid\":%ld,"
            "\"complete\":false,\"reason\":\"%s\"}\n", (long)owner, reasons[failure]);
    }
    if (length <= 0 || (size_t)length >= sizeof(report)) {
        return; /* An absent/truncated report is unavailable to the collector. */
    }
    size_t offset = 0;
    while (offset < (size_t)length) {
        ssize_t written = pwrite(report_fd, report + offset, (size_t)length - offset,
                                 (off_t)offset);
        if (written < 0 && errno == EINTR) {
            continue;
        }
        if (written <= 0) {
            return;
        }
        offset += (size_t)written;
    }
}

QEMU_PLUGIN_EXPORT int qemu_plugin_install(qemu_plugin_id_t id,
    const qemu_info_t *info, int argc, char **argv)
{
    if (installed || !info || info->system_emulation || !info->target_name ||
        strcmp(info->target_name, "x86_64") != 0 || info->version.min > 4 ||
        info->version.cur != 4 || argc != 1 || strncmp(argv[0], "report-fd=", 10)) {
        (void)fputs("durante-qemu: requires x86-64 user plugin API 4 and report-fd=N\n", stderr);
        return -1;
    }
    const char *value = argv[0] + 10;
    if (*value < '1' || *value > '9') {
        return -1;
    }
    for (const char *p = value; *p; ++p) {
        if (*p < '0' || *p > '9') {
            return -1;
        }
    }
    errno = 0;
    char *end;
    long fd = strtol(value, &end, 10);
    if (errno || *end || fd < 3 || fd > INT_MAX) {
        return -1;
    }
    int flags = fcntl((int)fd, F_GETFL);
    struct stat st;
    if (flags < 0 || (flags & O_ACCMODE) == O_RDONLY || (flags & O_APPEND) ||
        fstat((int)fd, &st) != 0 || !S_ISREG(st.st_mode) || st.st_size != 0 ||
        st.st_nlink > 1) {
        (void)fputs("durante-qemu: requires a fresh writable regular report descriptor\n", stderr);
        return -1;
    }
    report_fd = (int)fd;
    owner = getpid();
    installed = true;
    qemu_plugin_register_vcpu_init_cb(id, cpu_started);
    qemu_plugin_register_vcpu_tb_trans_cb(id, translated);
    qemu_plugin_register_vcpu_syscall_cb(id, syscall_enter);
    qemu_plugin_register_vcpu_syscall_ret_cb(id, syscall_return);
    qemu_plugin_register_atexit_cb(id, finished, NULL);
    return 0;
}

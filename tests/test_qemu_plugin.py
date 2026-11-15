"""Exercise the real QEMU plugin with bounded host-API stubs.

The harness includes qemu/durante.c directly. Only QEMU callback registration
and delivery are simulated; descriptor validation, accounting, lifecycle state
and report formatting use the production implementation. These tests do not
establish guest containment or replace execution against the prepared QEMU.
"""
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

static ssize_t test_pwrite(int fd, const void *buffer, size_t size, off_t offset);
#define pwrite test_pwrite
#include PLUGIN_SOURCE
#undef pwrite

static qemu_plugin_vcpu_simple_cb_t init_callback;
static qemu_plugin_vcpu_tb_trans_cb_t translate_callback;
static qemu_plugin_vcpu_syscall_cb_t syscall_callback;
static qemu_plugin_vcpu_syscall_ret_cb_t return_callback;
static qemu_plugin_udata_cb_t exit_callback;
static void *exit_data;
static unsigned registrations, write_calls;
static const char *write_mode = "normal";
static const qemu_plugin_id_t plugin_id = 17;

struct qemu_plugin_insn {
    qemu_plugin_vcpu_udata_cb_t callback;
    void *data;
};
struct qemu_plugin_tb {
    size_t count;
    struct qemu_plugin_insn instructions[4];
};

void qemu_plugin_register_vcpu_init_cb(qemu_plugin_id_t id,
    qemu_plugin_vcpu_simple_cb_t callback)
{
    assert(id == plugin_id);
    init_callback = callback;
    ++registrations;
}
void qemu_plugin_register_vcpu_tb_trans_cb(qemu_plugin_id_t id,
    qemu_plugin_vcpu_tb_trans_cb_t callback)
{
    assert(id == plugin_id);
    translate_callback = callback;
    ++registrations;
}
void qemu_plugin_register_vcpu_syscall_cb(qemu_plugin_id_t id,
    qemu_plugin_vcpu_syscall_cb_t callback)
{
    assert(id == plugin_id);
    syscall_callback = callback;
    ++registrations;
}
void qemu_plugin_register_vcpu_syscall_ret_cb(qemu_plugin_id_t id,
    qemu_plugin_vcpu_syscall_ret_cb_t callback)
{
    assert(id == plugin_id);
    return_callback = callback;
    ++registrations;
}
void qemu_plugin_register_atexit_cb(qemu_plugin_id_t id,
    qemu_plugin_udata_cb_t callback, void *data)
{
    assert(id == plugin_id);
    exit_callback = callback;
    exit_data = data;
    ++registrations;
}
size_t qemu_plugin_tb_n_insns(const struct qemu_plugin_tb *tb)
{
    return tb->count;
}
struct qemu_plugin_insn *qemu_plugin_tb_get_insn(const struct qemu_plugin_tb *tb,
    size_t index)
{
    assert(index < tb->count);
    return (struct qemu_plugin_insn *)&tb->instructions[index];
}
void qemu_plugin_register_vcpu_insn_exec_cb(struct qemu_plugin_insn *insn,
    qemu_plugin_vcpu_udata_cb_t callback, enum qemu_plugin_cb_flags flags,
    void *data)
{
    assert(flags == QEMU_PLUGIN_CB_NO_REGS && insn->callback == NULL);
    insn->callback = callback;
    insn->data = data;
}

static ssize_t test_pwrite(int fd, const void *buffer, size_t size, off_t offset)
{
    ++write_calls;
    if (!strcmp(write_mode, "eintr") && write_calls <= 3) {
        errno = EINTR;
        return -1;
    }
    if (!strcmp(write_mode, "error") ||
        (!strcmp(write_mode, "partial-error") && write_calls > 1)) {
        errno = EIO;
        return -1;
    }
    if (!strcmp(write_mode, "zero")) {
        return 0;
    }
    if ((!strcmp(write_mode, "partial") || !strcmp(write_mode, "partial-error"))
        && size > 7) {
        size = 7;
    }
    return pwrite(fd, buffer, size, offset);
}

static void syscall_at(unsigned cpu, int64_t number, uint64_t argument)
{
    syscall_callback(plugin_id, cpu, number, argument, 0, 0, 0, 0, 0, 0, 0);
}
static void execute(struct qemu_plugin_tb *tb, unsigned cpu, size_t index)
{
    assert(index < tb->count && tb->instructions[index].callback != NULL);
    tb->instructions[index].callback(cpu, tb->instructions[index].data);
}
static ssize_t read_report(int fd, char *buffer, size_t size)
{
    struct stat st;
    assert(fstat(fd, &st) == 0 && st.st_size >= 0 && (uint64_t)st.st_size < size);
    ssize_t length = pread(fd, buffer, size - 1, 0);
    assert(length >= 0);
    buffer[length] = 0;
    return length;
}
static void expect_complete(int fd, uint64_t count, unsigned code)
{
    char actual[1024], expected[1024];
    read_report(fd, actual, sizeof(actual));
    int length = snprintf(expected, sizeof(expected),
        "{\"schema\":1,\"kind\":\"durante-qemu-work\",\"pid\":%ld,"
        "\"complete\":true,\"instructions\":%" PRIu64 ",\"exit_code\":%u}\n",
        (long)getpid(), count, code);
    assert(length > 0 && (size_t)length < sizeof(expected));
    assert(!strcmp(actual, expected));
}
static void expect_incomplete(int fd, const char *reason)
{
    char actual[1024], expected[1024];
    read_report(fd, actual, sizeof(actual));
    int length = snprintf(expected, sizeof(expected),
        "{\"schema\":1,\"kind\":\"durante-qemu-work\",\"pid\":%ld,"
        "\"complete\":false,\"reason\":\"%s\"}\n", (long)getpid(), reason);
    assert(length > 0 && (size_t)length < sizeof(expected));
    assert(!strcmp(actual, expected));
    assert(strstr(actual, "\"instructions\":") == NULL && strstr(actual, "\"exit_code\":") == NULL);
}

int main(int argc, char **argv)
{
    assert(argc == 3);
    const char *test = argv[1];
    char path[PATH_MAX], option[96];
    int length = snprintf(path, sizeof(path), "%s/report-XXXXXX", argv[2]);
    assert(length > 0 && (size_t)length < sizeof(path));
    int fd = mkstemp(path);
    assert(fd >= 3);
    snprintf(option, sizeof(option), "report-fd=%d", fd);
    char *options[] = { option, option };
    qemu_info_t info = { .target_name = "x86_64", .version = { .min = 0, .cur = 4 } };
    const qemu_info_t *provided_info = &info;
    int option_count = 1;
    bool expect_rejected = false;

    if (!strncmp(test, "install-", 8)) {
        const char *kind = test + 8;
        expect_rejected = true;
        if (!strcmp(kind, "no-info")) provided_info = NULL;
        else if (!strcmp(kind, "no-target")) info.target_name = NULL;
        else if (!strcmp(kind, "target")) info.target_name = "aarch64";
        else if (!strcmp(kind, "system")) info.system_emulation = true;
        else if (!strcmp(kind, "old-api")) info.version.cur = 3;
        else if (!strcmp(kind, "future-api")) info.version.cur = 5;
        else if (!strcmp(kind, "min-api")) info.version.min = 5;
        else if (!strcmp(kind, "no-option")) option_count = 0;
        else if (!strcmp(kind, "duplicate-option")) option_count = 2;
        else if (!strcmp(kind, "unknown-option")) strcpy(option, "other=3");
        else if (!strcmp(kind, "empty")) strcpy(option, "report-fd=");
        else if (!strcmp(kind, "zero")) strcpy(option, "report-fd=0");
        else if (!strcmp(kind, "stdio")) strcpy(option, "report-fd=2");
        else if (!strcmp(kind, "negative")) strcpy(option, "report-fd=-3");
        else if (!strcmp(kind, "plus")) snprintf(option, sizeof(option), "report-fd=+%d", fd);
        else if (!strcmp(kind, "leading-zero")) snprintf(option, sizeof(option), "report-fd=0%d", fd);
        else if (!strcmp(kind, "space")) snprintf(option, sizeof(option), "report-fd= %d", fd);
        else if (!strcmp(kind, "trailing")) snprintf(option, sizeof(option), "report-fd=%dx", fd);
        else if (!strcmp(kind, "newline")) snprintf(option, sizeof(option), "report-fd=%d\n", fd);
        else if (!strcmp(kind, "int-overflow")) strcpy(option, "report-fd=2147483648");
        else if (!strcmp(kind, "long-overflow")) strcpy(option, "report-fd=99999999999999999999999999999999");
        else if (!strcmp(kind, "closed")) { assert(close(fd) == 0); fd = -1; }
        else if (!strcmp(kind, "nonempty")) assert(write(fd, "old", 3) == 3);
        else if (!strcmp(kind, "append")) assert(fcntl(fd, F_SETFL, O_APPEND) == 0);
        else if (!strcmp(kind, "readonly")) {
            int read_only = open(path, O_RDONLY);
            assert(read_only >= 3);
            snprintf(option, sizeof(option), "report-fd=%d", read_only);
        } else if (!strcmp(kind, "nonregular")) {
            int device = open("/dev/null", O_WRONLY);
            assert(device >= 3);
            snprintf(option, sizeof(option), "report-fd=%d", device);
        } else if (!strcmp(kind, "pipe")) {
            int pipe_fds[2];
            assert(pipe(pipe_fds) == 0);
            snprintf(option, sizeof(option), "report-fd=%d", pipe_fds[1]);
        } else if (!strcmp(kind, "hardlink")) {
            char alias[PATH_MAX];
            int size = snprintf(alias, sizeof(alias), "%s/alias", argv[2]);
            assert(size > 0 && (size_t)size < sizeof(alias));
            assert(link(path, alias) == 0);
        } else if (!strcmp(kind, "writeonly")) {
            int write_only = open(path, O_WRONLY);
            assert(write_only >= 3);
            snprintf(option, sizeof(option), "report-fd=%d", write_only);
            expect_rejected = false;
        } else if (!strcmp(kind, "unlinked")) {
            assert(unlink(path) == 0);
            expect_rejected = false;
        } else if (!strcmp(kind, "cloexec")) {
            assert(fcntl(fd, F_SETFD, FD_CLOEXEC) == 0);
            expect_rejected = false;
        } else { assert(!"unknown install case"); }
    }

    int result = qemu_plugin_install(plugin_id, provided_info, option_count, options);
    if (expect_rejected) {
        assert(result == -1 && registrations == 0 && !installed);
        return 0;
    }
    assert(result == 0 && registrations == 5 && installed);
    assert(init_callback && translate_callback && syscall_callback && return_callback && exit_callback);
    if (!strncmp(test, "install-", 8)) return 0;

    if (!strcmp(test, "duplicate-install")) {
        int replacement = open("/dev/null", O_WRONLY);
        assert(replacement >= 3);
        snprintf(option, sizeof(option), "report-fd=%d", replacement);
        assert(qemu_plugin_install(plugin_id, &info, 1, options) == -1);
        assert(registrations == 5 && report_fd == fd);
        return 0;
    }
    struct qemu_plugin_tb tb = { .count = 4 };
    translate_callback(plugin_id, &tb);
    assert(atomic_load(&instructions) == 0); /* Translation is not execution. */
    for (size_t i = 0; i < tb.count; ++i) assert(tb.instructions[i].callback != NULL);
    if (strcmp(test, "no-cpu")) init_callback(plugin_id, 0);
    if (strcmp(test, "no-instructions")) {
        execute(&tb, 0, 0);
        execute(&tb, 0, 2); /* Only actual callbacks count; skip other translated bytes. */
        execute(&tb, 0, 0);
    }
    const char *reason = NULL;
    unsigned code = 0;

    if (!strcmp(test, "normal") || !strcmp(test, "duplicate-finalize") ||
        !strcmp(test, "parent-only") || !strcmp(test, "unrelated-return")) {
        syscall_at(0, 60, 0);
    } else if (!strcmp(test, "exit-group")) {
        code = 126;
        syscall_at(0, 231, code);
    } else if (!strcmp(test, "exit-truncated")) {
        code = 255;
        syscall_at(0, 60, UINT64_MAX);
    } else if (!strcmp(test, "no-exit")) {
        reason = "no normal guest exit";
    } else if (!strcmp(test, "no-instructions")) {
        syscall_at(0, 60, 0);
        reason = "no guest instructions";
    } else if (!strcmp(test, "no-cpu")) {
        syscall_at(0, 60, 0);
        reason = "multiple guest threads";
    } else if (!strcmp(test, "overflow")) {
        atomic_store(&instructions, UINT64_MAX);
        execute(&tb, 0, 0);
        execute(&tb, 0, 0); /* Overflow remains sticky after wraparound. */
        syscall_at(0, 60, 0);
        reason = "instruction counter overflow";
    } else if (!strncmp(test, "restarted-", 10)) {
        int64_t number = !strcmp(test + 10, "exit") ? 60 : 231;
        syscall_at(0, number, 37);
        return_callback(plugin_id, 0, number, -512);
        reason = "no normal guest exit";
    } else if (!strcmp(test, "restart-then-exit")) {
        syscall_at(0, 60, 37);
        return_callback(plugin_id, 0, 60, -512);
        code = 9;
        syscall_at(0, 231, code);
    } else if (!strncmp(test, "process-", 8)) {
        int64_t number = strtoll(test + 8, NULL, 10);
        syscall_at(0, number, 0);
        return_callback(plugin_id, 0, number, -38); /* Failed attempts still invalidate. */
        syscall_at(0, 60, 0);
        reason = "guest process creation or exec attempted";
    } else if (!strncmp(test, "multicpu-", 9)) {
        const char *operation = test + 9;
        if (!strcmp(operation, "init")) init_callback(plugin_id, 1);
        else if (!strcmp(operation, "duplicate-init")) init_callback(plugin_id, 0);
        else if (!strcmp(operation, "instruction")) execute(&tb, 1, 0);
        else if (!strcmp(operation, "syscall")) syscall_at(1, 60, 123);
        else assert(!"unknown multicpu case");
        syscall_at(0, 60, 0);
        reason = "multiple guest threads";
    } else if (!strncmp(test, "write-", 6)) {
        write_mode = test + 6;
        syscall_at(0, 60, 0);
    } else { assert(!"unknown lifecycle case"); }

    if (!strcmp(test, "unrelated-return")) return_callback(plugin_id, 0, 1, 0);
    if (!strcmp(test, "parent-only")) {
        pid_t child = fork();
        assert(child >= 0);
        if (child == 0) {
            exit_callback(plugin_id, exit_data);
            _exit(0);
        }
        int status;
        assert(waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 0);
        char empty[1024];
        assert(read_report(fd, empty, sizeof(empty)) == 0);
    }
    exit_callback(plugin_id, exit_data);
    if (!strcmp(test, "duplicate-finalize")) {
        unsigned before = write_calls;
        execute(&tb, 0, 1);
        syscall_at(0, 60, 73);
        exit_callback(plugin_id, exit_data);
        assert(write_calls == before);
    }
    if (!strcmp(write_mode, "error") || !strcmp(write_mode, "zero") ||
        !strcmp(write_mode, "partial-error")) {
        char partial[1024];
        ssize_t size = read_report(fd, partial, sizeof(partial));
        assert(size == (!strcmp(write_mode, "partial-error") ? 7 : 0));
        unsigned before = write_calls;
        exit_callback(plugin_id, exit_data);
        assert(write_calls == before);
    } else if (reason) {
        expect_incomplete(fd, reason);
    } else {
        expect_complete(fd, 3, code);
        if (!strcmp(write_mode, "eintr")) assert(write_calls == 4);
        if (!strcmp(write_mode, "partial")) assert(write_calls > 1);
    }
    return 0;
}
'''


@unittest.skipUnless(platform.system() == "Linux", "requires Linux descriptors and process APIs")
class QemuPluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which(os.environ.get("CC", "cc"))
        if compiler is None:
            raise unittest.SkipTest("requires a C compiler")
        cls.directory = tempfile.TemporaryDirectory(prefix="durante-qemu-unit-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)
        source = cls.root / "harness.c"
        source.write_text(HARNESS)
        cls.binary = cls.root / "harness"
        subprocess.run(
            [compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
             "-D_FORTIFY_SOURCE=2", f'-DPLUGIN_SOURCE="{ROOT / "qemu/durante.c"}"',
             str(source), "-o", str(cls.binary)],
            check=True, capture_output=True, text=True, timeout=30,
        )

    def cases(self, names):
        for name in names:
            with self.subTest(case=name), tempfile.TemporaryDirectory(dir=self.root) as directory:
                result = subprocess.run(
                    [str(self.binary), name, directory],
                    capture_output=True, text=True, timeout=5,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_normal_exit_and_exact_report(self):
        self.cases(["normal", "exit-group", "exit-truncated", "unrelated-return"])

    def test_translation_and_finalization_lifecycle(self):
        self.cases(["no-exit", "no-instructions", "no-cpu", "duplicate-finalize", "parent-only"])

    def test_restarted_exit_is_not_completion(self):
        self.cases(["restarted-exit", "restarted-exit-group", "restart-then-exit"])

    def test_overflow_is_sticky_and_omits_totals(self):
        self.cases(["overflow"])

    def test_process_changes_fail_closed_even_when_syscall_fails(self):
        self.cases([f"process-{number}" for number in [56, 57, 58, 59, 322, 435, -1, 0x4000003C]])

    def test_multiple_cpus_fail_closed(self):
        self.cases([f"multicpu-{kind}" for kind in ["init", "duplicate-init", "instruction", "syscall"]])

    def test_report_write_interruption_short_write_and_failure(self):
        self.cases([f"write-{kind}" for kind in ["eintr", "partial", "error", "zero", "partial-error"]])

    def test_install_validates_target_and_exact_api(self):
        self.cases([f"install-{kind}" for kind in [
            "no-info", "no-target", "target", "system", "old-api", "future-api", "min-api",
        ]])

    def test_install_validates_argument_grammar(self):
        self.cases([f"install-{kind}" for kind in [
            "no-option", "duplicate-option", "unknown-option", "empty", "zero", "stdio",
            "negative", "plus", "leading-zero", "space", "trailing", "newline",
            "int-overflow", "long-overflow",
        ]])

    def test_install_rejects_unsafe_descriptors(self):
        self.cases([f"install-{kind}" for kind in [
            "closed", "nonempty", "append", "readonly", "nonregular", "pipe", "hardlink",
        ]])

    def test_install_accepts_owned_regular_descriptor_variants(self):
        self.cases([f"install-{kind}" for kind in ["writeonly", "unlinked", "cloexec"]])

    def test_plugin_installation_cannot_replace_the_report_descriptor(self):
        self.cases(["duplicate-install"])


if __name__ == "__main__":
    unittest.main()

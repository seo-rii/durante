/*
 * Minimal declarations from QEMU 10.0's public plugin ABI (version 4).
 * https://gitlab.com/qemu-project/qemu/-/blob/v10.0.15/include/qemu/qemu-plugin.h
 * Copyright (C) 2017, Emilio G. Cota <cota@braap.org>
 * Copyright (C) 2019, Linaro
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Deliberately pinned: later QEMU plugin ABIs change callback signatures.
 */
#ifndef DURANTE_QEMU_PLUGIN_API_H
#define DURANTE_QEMU_PLUGIN_API_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define QEMU_PLUGIN_EXPORT __attribute__((visibility("default")))
#define QEMU_PLUGIN_VERSION 4
typedef uint64_t qemu_plugin_id_t;
typedef struct qemu_info_t {
    const char *target_name;
    struct { int min; int cur; } version;
    bool system_emulation;
    union { struct { int smp_vcpus; int max_vcpus; } system; };
} qemu_info_t;
struct qemu_plugin_tb;
struct qemu_plugin_insn;
enum qemu_plugin_cb_flags {
    QEMU_PLUGIN_CB_NO_REGS, QEMU_PLUGIN_CB_R_REGS, QEMU_PLUGIN_CB_RW_REGS,
};
typedef void (*qemu_plugin_udata_cb_t)(qemu_plugin_id_t, void *);
typedef void (*qemu_plugin_vcpu_simple_cb_t)(qemu_plugin_id_t, unsigned int);
typedef void (*qemu_plugin_vcpu_udata_cb_t)(unsigned int, void *);
typedef void (*qemu_plugin_vcpu_tb_trans_cb_t)(qemu_plugin_id_t, struct qemu_plugin_tb *);
typedef void (*qemu_plugin_vcpu_syscall_cb_t)(qemu_plugin_id_t, unsigned int,
    int64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t,
    uint64_t, uint64_t);
typedef void (*qemu_plugin_vcpu_syscall_ret_cb_t)(qemu_plugin_id_t, unsigned int,
    int64_t, int64_t);
void qemu_plugin_register_vcpu_init_cb(qemu_plugin_id_t, qemu_plugin_vcpu_simple_cb_t);
void qemu_plugin_register_vcpu_tb_trans_cb(qemu_plugin_id_t, qemu_plugin_vcpu_tb_trans_cb_t);
size_t qemu_plugin_tb_n_insns(const struct qemu_plugin_tb *);
struct qemu_plugin_insn *qemu_plugin_tb_get_insn(const struct qemu_plugin_tb *, size_t);
void qemu_plugin_register_vcpu_insn_exec_cb(struct qemu_plugin_insn *,
    qemu_plugin_vcpu_udata_cb_t, enum qemu_plugin_cb_flags, void *);
void qemu_plugin_register_vcpu_syscall_cb(qemu_plugin_id_t, qemu_plugin_vcpu_syscall_cb_t);
void qemu_plugin_register_vcpu_syscall_ret_cb(qemu_plugin_id_t, qemu_plugin_vcpu_syscall_ret_cb_t);
void qemu_plugin_register_atexit_cb(qemu_plugin_id_t, qemu_plugin_udata_cb_t, void *);
#endif

# QEMU guest work with CPU-feature restrictions

The optional Durante QEMU plugin records guest instruction execution callbacks
inside the emulator used by aonohako's CPU-feature policy. Loading both plugins
in one QEMU process preserves instruction blocking while measuring the submitted
program, including executed library and generated code. Wrapping QEMU with
Durante's native DynamoRIO client would instead count the emulator's host code.

This is an embedding interface. The native `run` and `isolate` commands and
their bundles retain their existing DynamoRIO behavior. The aonohako API still
rejects combined CPU restrictions and work measurement until a follow-up change
selects this backend and consumes its separate result contract.

## Build

Build on Linux x86-64 with a C11 compiler and Python 3.11 or newer:

```sh
python3 toolkit/build_qemu_plugin.py --output build/durante-qemu.so
cargo build --locked --release
```

The build script compiles only the plugin. It does not download QEMU, prepare a
sandbox or update a runtime bundle. Package `durante-qemu.so` separately with the
matching CLI. The plugin targets QEMU plugin API 4 and rejects other current API
versions, system emulation and non-x86-64 guests. The qualified runtime for this
composition is aonohako's patched QEMU 10.0.15, with its guest-memory bounds patch
and `cpu-policy.so`; a distribution's static QEMU executable may lack the plugin
loader and is not a substitute.

The QEMU plugin and its attributed ABI declarations under `qemu/` are licensed
GPL-2.0-or-later. This component is an exception to the repository's default
BSD-3-Clause license.

## Embed in the existing execution owner

Use the existing aonohako CPU-policy execution path and its static ELF64 x86-64
validation, host sandbox, syscall restrictions, CPU/RSS limits and cleanup.
The counter does not supply any of these protections. Keep the same fixed guest
address range and CPU model. The supported guest is one process with one thread;
process creation and exec attempts invalidate accounting even when they fail.
Retain the policy's x86-64-only syscall and mode-transition enforcement:
`int 0x80`, `sysenter` and compatibility-mode transitions are rejected by that
policy, not classified by this counter.

Before launching, the owner creates two distinct empty regular files in its
protected report directory, opened for writing without append mode. Inherit
only the designated report descriptors in addition to standard I/O. The policy
file receives `R`, `RV` or `RS`; the work file receives a separate completion
record. Neither report path nor descriptor may be accessible to the guest.
The existing policy's denial of non-stdio guest descriptor access remains
required for the new work descriptor too.

For example, with policy descriptor 3 and work descriptor 4 already prepared by
the owner, the inner emulator invocation is:

```sh
/trusted/cpu-policy/qemu-x86_64 \
  -B 0x10000000000 -R 4G -cpu qemu64 \
  -plugin /trusted/cpu-policy/cpu-policy.so,avx=on,report-fd=3 \
  -plugin /trusted/durante/durante-qemu.so,report-fd=4 \
  /work/contestant
```

Select `simd=on` in the policy plugin to prohibit packed SIMD as well. Durante
does not parse or weaken that policy. Plugin load order does not define the
verdict: either plugin can observe a callback first, and an interrupted or
policy-terminated measurement must be discarded as a whole.

The owner's completion sequence is:

1. Wait for the emulator and finish the existing resource checks and full
   execution-group cleanup. Retain its launched PID and actual wait status.
2. Reject a signal, timeout, forced stop, resource failure or cleanup failure.
   Require the independent CPU-policy report to be exactly `R`. `RV`, `RS`, an
   absent report or any other bytes do not permit a work result.
3. Collect the work report using the PID and normal exit code observed by the
   owner, and require the collector's `valid` and `complete` fields to be true.

```sh
durante collect-qemu --input /private/work-report.json \
  --root-pid 1234 --exit-code 0
```

The collector writes its result to stdout and exits 0 for a complete diagnostic
measurement, 1 for an unavailable one. The exit-code argument is the guest's
normal exit status, which can be nonzero. An ordinary guest exit 126 can produce
a complete result; a CPU-policy termination also uses 126 but has no acceptable
policy/completion pair. Never infer the policy verdict from the exit code or
stderr alone. The collector neither starts nor waits for a process and cannot
verify that the supplied PID, exit code or external policy decision are honest.

## Work semantics and result contract

The plugin attaches a callback to each translated guest instruction and counts
callbacks that actually execute. Translation alone adds no work. These are
pre-execution callbacks: a faulting instruction may be counted even when it does
not complete, and retries or signal handling can affect counts. They are not
hardware-retired instructions and are not interchangeable with the native
`bb_entry_app_instructions` metric.

QEMU implements some REP operations with internal loops and can reenter the
same guest instruction at chunk boundaries or after faults. Consequently the
counter promises neither one callback per original REP instruction nor one per
repeated element. REP byte demand is unavailable; the collector does not invent
zero byte totals or a disabled-telemetry claim. No CPU-time conversion or work
limit is selected by this backend.

A successful collection includes:

```json
{
  "schema": 1,
  "authority": "diagnostic_only",
  "metric": "guest_instruction_callbacks",
  "scope": "single_process_guest_user_code",
  "root_pid": 1234,
  "valid": true,
  "complete": true,
  "instructions": 42,
  "exit_code": 0,
  "rep_telemetry_status": "unavailable",
  "integrity_verified": false,
  "measurement_contract": {
    "version": 1,
    "backend": "qemu_tcg",
    "counter_integrity": "emulator_managed",
    "trusted_verdict_eligible": false,
    "instruction_semantics": "guest_instruction_callback",
    "simd_weighting": "one_per_callback",
    "cpu_time_conversion": null
  }
}
```

`emulator_managed` identifies where the counter lives; it does not attest the
runtime, sandbox, file provenance or counter integrity. Consumers must recognize
this contract explicitly. Do not pass its instruction total through an existing
DynamoRIO threshold or label it as a native process-tree measurement. Failures
retain the diagnostic contract with `valid: false`, `complete: false` and a
bounded reason, and expose no instruction total.

## Raw completion record

The plugin accepts exactly one option, `report-fd=N`, for a fresh writable
regular descriptor numbered at least 3. Read-only, append-mode, nonregular,
multiply linked and nonempty report files are rejected at plugin load.
On normal single-process guest exit it writes one LF-terminated JSON line:

```json
{"schema":1,"kind":"durante-qemu-work","pid":1234,"complete":true,"instructions":42,"exit_code":0}
```

`pid` is the host emulator PID. An incomplete run that reaches QEMU's final
callback may instead write:

```json
{"schema":1,"kind":"durante-qemu-work","pid":1234,"complete":false,"reason":"no normal guest exit"}
```

Forced termination and the CPU-policy plugin's immediate exit may leave the
file empty. QEMU's final callback also runs for some fatal guest signals, so
reaching it alone is insufficient: the plugin requires an observed guest
`exit`/`exit_group`, clears an interrupted exit attempt on syscall return, and
the collector matches the externally observed exit status. Counter overflow,
extra guest CPUs, process/exec attempts, zero callbacks and missing normal exit
yield no usable total. Fork children cannot overwrite the parent's report.

The collector requires one complete line of at most 1,024 bytes, schema 1,
the expected kind and PID, a positive unsigned 64-bit instruction count and
an exit code from 0 through 255. It rejects unknown or duplicate keys, missing
required fields, present nulls, wrong types, extra/truncated records and
success/failure field mixtures. File reads reject symlinks, multiple hard links,
nonregular files and detectable changes during the bounded read. These checks
do not authenticate an otherwise forged record.

## Regression coverage

`tests/test_qemu_plugin.py` exercises plugin lifecycle, overflow, descriptor
validation and report completion with controlled callbacks.
`tests/qemu_collector.rs` covers strict collection and the CLI.
`tests/qemu_policy.py` composes the guest counter with the actual aonohako policy
runtime and checks execution outcomes alongside guest counts. The composition
must remain covered when changing either runtime or plugin; native DynamoRIO
tests alone cannot validate this backend.

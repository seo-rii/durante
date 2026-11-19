# Durante DynamoRIO client

Build `libdurante.so` using the [repository instructions](../README.md).
Launch it with `-outdir ABSOLUTE_PRIVATE_DIRECTORY`, optionally followed by
`-rep_bytes`. Add `-disallow_cpu_feature avx` and/or
`-disallow_cpu_feature simd` to stop before selected instructions execute.
Add `-cpuid_profile x86-64-v1` or `x86-64-v2` to report one fixed virtual
processor to application CPUID/XGETBV ([CPUID profiles](../docs/CPUID_PROFILE.md)).
Values are exact lowercase spellings and duplicates are rejected. The earlier
single-file `drmeter -o REPORT` interface is not used.

Each application basic block adds its instruction count to thread-local state.
The ordinary counter can add a block's instructions directly to the executing
thread's counter. It saves and restores its scratch registers and arithmetic
flags even when a signal handler is the only code that reads them.
Between live work publications this avoids a callback on each block.
The callback still handles publication boundaries, replay and checked additions
near overflow. The latency model uses the callback to charge its block bound.

REP MOVS/STOS byte demand is read at the REP instruction after earlier register
updates, with one clean call per REP entry. Conditional REP forms are separately
reported as unsupported byte demand, without inventing a byte charge.

Fork callbacks reset inherited counters. Exec checkpoints preserve work in the
old image and new image records restart counters; failed exec continues its old
cumulative segment. Shared-address-space vfork children use context-local process
state instead of modifying parent globals. The collector selects each completed
segment once and verifies process edges and all terminal records.

Signals remain visible under a basic-block-entry definition; they do not establish
hardware retirement accuracy. Runtime compatibility, process-tree completeness,
counter trust and time-limit semantics are separate questions.

The policy applies at executed instruction boundaries, including generated code,
and is inherited by followed forks and threads. While enabled, application
`execve`/`execveat`, x32 syscalls and unsupported clone forms return `EPERM`;
compatibility syscall instructions stop before execution. Measurement-only
execution retains its existing syscall behavior. A feature violation writes a
`cpu_feature_violation` lifecycle record and exits that process with 126. The
collector returns unavailable work with no partial totals. A normal application
exit 126 does not establish a violation. Policy state and records share the
application's mutable trust domain; this is not tamper-resistant enforcement.

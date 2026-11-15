# Durante DynamoRIO client

Build `libdurante.so` using the [repository instructions](../README.md).
Launch it with `-outdir ABSOLUTE_PRIVATE_DIRECTORY`, optionally followed by
`-rep_bytes`. Add `-disallow_cpu_feature avx` and/or
`-disallow_cpu_feature simd` to stop before selected instructions execute.
Values are exact lowercase spellings and duplicates are rejected. The earlier
single-file `drmeter -o REPORT` interface is not used.

Each application basic block adds its instruction count to thread-local state.
A DynamoRIO clean call preserves application registers and arithmetic flags,
including values observed by a signal handler even when linear analysis considers
them dead. A checked addition marks per-thread instruction overflow permanently
invalid; aggregation checks all additional sums. This state preservation costs
more than the earlier inline counter and must be included in future overhead
benchmarks.
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
and is inherited by followed children and exec images. A violation writes a
`cpu_feature_violation` lifecycle record and exits that process with 126. The
collector returns unavailable work with no partial totals. A normal application
exit 126 does not establish a violation. Policy state and records share the
application's mutable trust domain; this is not tamper-resistant enforcement.

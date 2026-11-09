# Workmeter DynamoRIO client

Build `libworkmeter.so` using the [repository instructions](../README.md).
Launch it with `-outdir ABSOLUTE_PRIVATE_DIRECTORY`, optionally followed by
`-rep_bytes`. The earlier single-file `drmeter -o REPORT` interface is not used.

Each application basic block adds its instruction count to thread-local state.
Register and arithmetic-flag reservations use `drreg`. A carry accumulator detects
64-bit per-thread instruction overflow; aggregation checks all additional sums.
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

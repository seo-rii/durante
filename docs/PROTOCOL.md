# Durante lifecycle protocol, schema 1

The client is launched with `-outdir DIR [-rep_bytes]`, optionally followed by
one or two `-disallow_cpu_feature avx|simd` pairs. DIR must already exist,
be empty, private and writable by the target. `drrun -follow_children` must be
active. The root PID is the launched target/engine PID, preserved through exec.
The caller collects only after the entire tree has stopped and been reaped.

## Files and limits

Each PID writes `process-PID.jsonl`, one complete JSON object plus LF per event.
PID is a positive signed-32-bit-range integer. Exec appends new image segments to
the same process file; PID reuse after a completed process is rejected.

A reader must enforce: at most 256 directory entries; at most 1 MiB per file;
at most 16 MiB total; at most 4,096 events per process; fewer than 3,072 bytes per
line. Enumerate at most 257 entries before rejecting, without an unbounded list.
Reject unexpected names, symlinks, nonregular files, files with multiple hard
links, invalid UTF-8, duplicate JSON keys, nonfinite numbers, truncated lines,
changing size/mtime/ctime/link count, or any field outside its type/range.
Numbers must be integers, never booleans. Counters use unsigned 64-bit bounds.

The client also bounds its writes and opens directory/file leaves with
`O_NOFOLLOW`; collector protections remain necessary. These checks protect the
collector from malformed input, not from a target capable of forging its own
in-process counters or files. `authority` is always `diagnostic_only`.

## Common event fields

Every record contains:

- `schema: 1`, `client_version: 1`, `authority: "diagnostic_only"`.
- `metric: "bb_entry_app_instructions"`.
- `pid`: matching the filename.
- `segment`: a PID-prefixed string, currently `PID-MICROSECONDS`; treat it as an
  opaque unique image ID, not a measurement of duration.
- `seq`: exactly 1, 2, ... within that image, restarting only at a new `start`.
- `event`: one of the events below.

Duplicate segment IDs, sequence gaps/replays or unrecognized events are invalid.

## Lifecycle events

`start` has `origin`, `parent_pid`, `parent_segment`, `rep_bytes_enabled` and
`sdk_version: "11.3.0"`. The root's first start has origin `initial`, parent 0 and
empty parent segment. A child's first start has origin `fork` and the actual
parent image ID. This includes a vfork child's pre-exec work. Each new exec image
has origin `initial`, parent 0 and empty parent segment. All images must agree
on REP mode.

New clients also advertise `rep_breakdown_version: 1` on every `start`.
All images and processes must agree on its presence and version. An absent
capability denotes legacy telemetry with an unspecified operation split; it
does not imply zero MOVS or STOS work. Unknown versions and mixed legacy/new
trees are unavailable to collectors that understand the extension.

Enabled native CPU restrictions add `cpu_policy_version: 1` and `cpu_policy`
to every `start`. The latter is exactly `"avx"`, `"simd"` or `"avx,simd"`;
both fields are absent when disabled. All processes and images must agree on
the capability, version and canonical policy. Partial metadata, unknown
versions and mixed policy trees are unavailable. A successful aggregate adds
the corresponding canonical `disallowed_cpu_features` array. Legacy aggregates
omit it; absence must not satisfy an explicitly requested policy.

An executed forbidden instruction produces `cpu_feature_violation` with
`feature: "avx"` or `"simd"` in its live image, with the normal next sequence
number, before process exit 126. The feature must belong to the announced
policy. This event makes the whole measurement unavailable, without any partial
counter totals, and returns `reason: "disallowed CPU feature: avx"` (or `simd`).
No final `end` is required after this event. Exit code 126 by itself is not a
violation. These events and policy metadata remain application-mutable.

`spawn` is written by the parent only after successful process creation. Fields
are `child_pid`, Linux x86-64 `syscall` number (clone 56, fork 57, vfork 58, clone3
435), and boolean `supported`. Unsupported edges invalidate the total. Failed
creation makes no edge. A child can run/write before its parent's post-syscall
callback, so graph validation must not depend on cross-file write ordering.

`exec_attempt` contains a cumulative checkpoint of the old image's counters.
A successful exec has no old-image process-exit callback: its next event is a
new segment's `start` with sequence 1. The collector then selects the checkpoint
as that old image's terminal value. A multithreaded exec checkpoint is invalid
because other live thread work is not safely snapshotted.

`exec_failed` contains `syscall` (59 or 322) and negative `result`. It cancels the
pending checkpoint. Later cumulative work includes that attempted exec already;
therefore the collector discards the checkpoint and eventually uses `end`.

`end` contains the final cumulative counters, thread completion and application
exit metadata. No event or new start may follow a final end for the same PID.
`detached`, missing final images, dangling exec attempts, missing child records
and forced termination make the entire tree unavailable.

## Terminal/checkpoint fields

Unsigned counters: `instructions`, `rep_bytes`, `rep_entries`,
`unsupported_rep_entries`, `signals_observed`, `threads_started`,
`threads_finished`. A complete selected terminal has positive instruction work,
positive equal thread counts, `threads_complete: true`, `accounting_error: false`
and `valid: true`. REP fields must be zero if REP mode is false.

`quality` is `uninterrupted` for zero observed signals, otherwise
`signals_observed`. The latter preserves a complete **BB-entry** work metric,
not exact retired instructions. `app_exit_seen` must be true for an end;
`app_exit_code` is an integer from 0 through 255. Nonzero exit codes do not discard
completed work. Exec checkpoints do not require an application exit event.

For advertised breakdown version 1, a selected `end` or successful
`exec_attempt` checkpoint also requires these four scalar fields:

```json
{
  "rep_movs_bytes": 65,
  "rep_stos_bytes": 66,
  "rep_movs_entries": 3,
  "rep_stos_entries": 3
}
```

Each counter is an unsigned 64-bit integer. MOVS and STOS bytes must sum exactly
to `rep_bytes`, and their entry counts to `rep_entries`, without overflow.
Nonzero bytes of either kind require a nonzero entry count of that kind. A
zero-RCX instruction contributes one entry and zero bytes. Disabled REP
telemetry emits explicit zeros for all four counters. Unsupported REP operations
remain in `unsupported_rep_entries` and do not enter either supported bucket.

`rep_movs_bytes` is requested copy length, not source reads plus destination writes;
`rep_stos_bytes` is requested fill length. Element widths 1/2/4/8, direction and
RCX/ECX addressing use the same existing byte-demand semantics. These counters
do not assert completed transfers, cache misses, memory bandwidth or elapsed
time. Internal code replay retains each original request once in its original
operation bucket, including partially completed REP retries.

Present raw counters always receive strict unsigned integer type checks.
Capability, required presence and arithmetic checks apply to selected terminal
values. A failed exec checkpoint is discarded as before; its provisional
counters do not enter the final total. Legacy selected terminals omit all four
fields. Raw events retain their scalar-only format and schema/client version 1:
older collectors can still compute
the unchanged combined totals, but cannot claim operation-level telemetry.

## Aggregate

Verify every nonroot process has exactly one successful spawn edge whose parent
PID and parent image match its first start. No missing/unannounced child, duplicate
edge, disconnected cycle, mixed mode, duplicate image or incomplete process is
allowed. Every process must be reachable from the root. Then add each selected
image's terminal counters exactly once, checking unsigned overflow at each sum.

A successful result has `schema: 1`, `authority: "diagnostic_only"`,
`metric: "bb_entry_app_instructions"`, `scope: "process_tree_user_code"`,
`valid: true`, `complete: true`, `root_pid`, `process_count`, `image_count`,
`rep_bytes_enabled`, `quality`, and the five counter totals. `processes` and
`segments` preserve the selected components for auditing. No CPU-ms conversion
is performed. Failure returns `valid: false`, `complete: false` and a bounded
reason; it must not expose partial totals as a valid measurement.

When the complete tree advertises the extension, `rep_breakdown` is also emitted
on the aggregate, every process and every selected image segment. All four
counters are checked and summed at each level. Legacy aggregates omit the object
at every level; failed aggregates expose none of it. `isolate` independently
checks these partitions and component sums before retaining an inner report.
The aggregate's object is a closed, versioned structure, for example:

```json
"rep_breakdown": {
  "version": 1,
  "movs_bytes": 65,
  "stos_bytes": 66,
  "movs_entries": 3,
  "stos_entries": 3
}
```

## Result contract and consumers

The collector, `run` envelope and `isolate` envelope all emit
`integrity_verified: false` and this locally constructed `measurement_contract`:

```json
{
  "version": 1,
  "backend": "dynamorio",
  "counter_integrity": "application_mutable",
  "trusted_verdict_eligible": false,
  "instruction_semantics": "basic_block_entry",
  "simd_weighting": "one_per_instruction",
  "cpu_time_conversion": null
}
```

These are additive schema-1 result fields; the raw lifecycle event format is
unchanged. They describe semantics, not an attestation. A target can forge a
structurally complete stream. `valid` and `complete` only report structural and
lifecycle consistency; they never imply tamper resistance, exact retired
instructions or an eligible trusted verdict. Successful containment also leaves
the contract unchanged. Unknown or absent contracts must not acquire trust by
default. A caller must not derive TLE, billing or a security decision from these
counts even when all validation fields indicate success. Signal interruption can
charge the unexecuted tail of an entered basic block. SIMD width and active lanes
do not multiply its instruction charge; CPU milliseconds have no defined
conversion.

The prepared runtime distinguishes internal code-modification re-entry from an
application signal. A fault retry rolls back the charged but unexecuted suffix
of the old block, then charges the new entry; a completed sandbox write retains
that write and rolls back only its tail. A sandbox check before client entry has
no charge to undo. Partially completed REP MOVS/STOS retain their original
requested byte demand and entry once across internal retries. These corrections
fix duplicated instrumentation, not general signal-interruption overestimation.
An unknown/mismatched checkpoint, interrupted pending retry or arithmetic error
sets sticky accounting invalidity. Thread completion, exec checkpoints and
shared-address-space vfork child records all preserve that invalidity. Raw schema
and the diagnostic-only contract remain unchanged.

Every aggregate also emits `rep_telemetry_status`, calculated from validated
counters rather than target-supplied labels:

| Value | Meaning |
| --- | --- |
| `disabled` | REP mode is off and all three REP counters are zero. |
| `movs_stos_only` | REP mode is on and no unsupported REP entries were observed; coverage is limited to requested MOVS/STOS bytes. |
| `partial` | REP mode is on and `unsupported_rep_entries` is positive. Instruction accounting can still be complete. |
| `unavailable` | The measurement failed validation or execution; no counter totals are exposed. |

`movs_stos_only` does not certify all REP forms or successful completion of each
requested transfer. A partial result retains its unsupported-entry total, and
`isolate` accepts it only when all component sums and its explicit coverage label
agree. A missing status in an older schema-1 report means unspecified coverage;
it must not be interpreted as complete coverage. Execution status, instruction
validity, REP coverage and counter integrity are separate decisions.

## Operation-breakdown qualification

`tests/smoke.py` checks MOVS/STOS widths and zero-count entries individually.
`tests/rep_breakdown.py` exercises a fixed mixed workload through ordinary
execution, fork, vfork, thread completion, exec and failed exec. Native and
engine-only controls validate memory/output; release/debug counter modes and
the supervisor validate exact per-image, per-process and total operation demand.
The separate code-replay suite checks preserved MOVS/STOS attribution across
partial forward/backward copies, fills and repeated code replacement.

Shared protocol vectors, grammar fuzzing and Rust/Python differential mutation
tests cover the new optional capability and partition arithmetic. The new fields
are a prerequisite for models with different copy/fill coefficients. They do not
provide coefficients or qualify a time conversion; instruction-mix differences
such as branches and sorting remain separate model work.

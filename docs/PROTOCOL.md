# Durante lifecycle protocol, schema 1

The client is launched with `-outdir DIR [-rep_bytes]`. DIR must already exist,
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

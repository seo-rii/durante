# Online work budgets

`durante run` and `durante isolate` accept an optional `--work-budget N` alongside
the wall deadline. The v1 unit is one basic-block-entry application instruction
or one requested byte of REP MOVS/STOS. A budget of zero is valid. The owner
stops the measured tree when the sum of the published components is strictly
greater than N. Equality continues. Use a separate, generous wall deadline to
bound idle time and periods without eligible progress.

```sh
durante run --drrun "$DRRUN" --client "$CLIENT" --report run.json \
  --work-budget 2000000 --timeout-ms 15000 -- ./program
```

## Reading a result

| Observation | Execution result | Work evidence |
| --- | --- | --- |
| The tree exited within budget | Normal completion | Terminal instruction, MOVS and STOS counts must match the live file after reaping. |
| A prefix exceeded the budget while the tree was running | `work_budget_exceeded`, exit 123 | `work_budget.observation = "live"` and the prefix that triggered the stop. The forced stop has no accepted terminal measurement. |
| A short program exited over budget between polls | `work_budget_exceeded`, exit 123 | `observation = "terminal"` after reconciling all three complete counts. |
| Wall deadline | `timed_out`, exit 124 for `run` | `work_budget.status = "unavailable"`. Cancellation and raw isolation limits also retain their own causes. |

Check both process-tree cleanup and record cleanup in the execution envelope.
An accounting or cleanup failure can invalidate an apparent crossing.
The isolated owner validates the requested capability, receipt shape, cleanup
and inner exit state before relaying a budget result.

The CLI's sum uses a wider integer than the budget. Individual counters
saturate at `UINT64_MAX` with an overflow bit for that component. The three
counters are also available separately to consumers with a bound time model.
A work unit alone has no millisecond conversion. This capability retains
Durante's application-mutable, diagnostic-only counter contract.

## Live client contract

The owner creates a fresh zero-filled 48-byte regular file outside the terminal
records directory and passes `-rep_bytes -live_work_v1 /absolute/path` to the
client. It retains a descriptor for that run. The Linux x86-64 layout is six
aligned little-endian uint64 words in this order.

| Offset | Meaning |
| ---: | --- |
| 0 | Ready, published after client initialization |
| 8 | Sticky invalidity |
| 16 | Instructions published |
| 24 | Requested MOVS bytes published |
| 32 | Requested STOS bytes published |
| 40 | Sticky overflow bits for instructions (1), MOVS (2), STOS (4) |

All instrumented images, processes and threads in the role contribute to that
one file. Each thread advances its private published baseline; shared counters
use atomic additions. A child starts fresh, and an exec checkpoint publishes
only the outstanding delta. A failed exec therefore continues against the
same baseline. Publication at the next block entry leaves the current block
available for replay correction. The client batches each component up to a
4096-unit publication threshold. Before each application syscall it flushes
eligible pending work, including when a thread is about to wait, exit or exec.
A thread that another thread's `exit_group` stops publishes its remainder from
its exit event, so the total includes the final block prefix that its terminal
record also counts. Signals and CMPS/SCAS entries keep the page usable, as they
keep the terminal record valid: a CMPS/SCAS entry charges its one instruction
and no bytes, because the unit has no CMPS/SCAS bytes.

An owner whose weighted time policy rejects signals and unsupported REP entries
can add the client option `-live_work_require_eligible` to `-live_work_v1`.
The first observed signal or CMPS/SCAS entry in any participating thread or
process sets the shared invalidity word. The owner must then use its external
deadline. The option affects only the live page. A naturally completed role
still reports its counters and quality fields for terminal evaluation. A budget
decision made before such an event covers only the observed execution prefix.

Accounting errors, a replay that cannot be settled, and exec while other
threads still run also invalidate live eligibility. Missing or inconsistent progress leaves the external deadline
active. A budgeted natural completion requires a matching terminal report.

Components may be read at slightly different instants. Their sum provides a
lower bound at the last sampled instant. Publication batching, the owner poll
interval, concurrent tasks, scheduling and a single large REP operation can
all increase overshoot. The protocol gives no fixed bound on the instructions
executed between crossing and the entire role stopping.

## Runtime verification

Run the owner checks with a prepared core and the client built from this tree.

```sh
python3 tests/online_work_clock.py --runner "$DURANTE" --drrun "$DRRUN" --client "$CLIENT"
```

The harness checks an independently counted three-instruction boundary, an
short above-budget work followed by a blocking syscall, an infinite loop,
a child consuming the budget while its parent waits, process
reaping and an idle tree reaching the raw wall deadline. Additional client
tests exercise lifecycle aggregation and replay corrections. The isolation
smoke suite exercises the same option under its bounded namespace profile.

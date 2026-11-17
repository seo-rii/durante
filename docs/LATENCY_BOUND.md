# Static latency bound

`durante run --latency-bound` (and `isolate --latency-bound`; client option
`-latency_bound`) adds a second counter beside the instruction count: a static
per-basic-block cost in sixteenths of a cycle, computed once when a block is
translated and charged at block entry, exactly where its instructions are
counted. It exists because instruction counts alone cannot be converted to
milliseconds: blocks with the same instruction mix can differ several times in
speed depending on their dependency chains.

It is a diagnostic model input like the instruction count. It is not a
prediction of any real processor's time, not a verdict, and, like every
in-process counter, it can be changed by the measured application.

## Model, version 1

`client/latency_bound.h` defines it; the test client and the production client
include the same header.

- Latency: integer multiply 3 cycles, integer divide 20, scalar FP divide and
  square root 14, the listed FP add/subtract/multiply/convert instructions and
  any other instruction DynamoRIO classifies as floating point 4, all others 1.
- A memory source is ready 4 cycles after its address registers. A store waits
  for its address registers. `lea` and multi-byte `nop` only compute addresses.
- Only register and flag dependencies are tracked (general-purpose
  subregisters share their full register). Memory dependencies, execution
  ports, caches, branch prediction and the front end are not modeled.
- `bound = max(issue, recurrence)`. `issue` is the block's instruction count
  divided by 4 (a 4-wide machine), so 4/16 cycle per instruction. For a block
  whose final conditional branch targets its own start, `recurrence` is the
  growth of its critical path per iteration, measured between the 4th and 8th
  of eight back-to-back passes; other blocks have none.
- An emulation region (for example a virtualized CPUID) counts as one
  instruction and contributes its single placeholder (latency 1, no operands).

A block that cannot be analyzed (more than 65,536 instructions, or the
analysis buffer cannot be allocated) makes the image's accounting invalid
rather than charging nothing.

When code-cache replay returns the unexecuted suffix of a block (a fault in the
middle of a block that was charged at entry), the bound returns the same
proportion: `floor(bound * returned_instructions / block_instructions)`. Every
block therefore keeps at least its issue cost, so a valid terminal always has
`latency_bound_x16 >= 4 * instructions`; the collectors reject anything less.

## Records and aggregate

With the option, every `start` adds `latency_bound_version: 1`, and every `end`
and `exec_attempt` checkpoint adds `latency_bound_x16`, an unsigned 64-bit
counter. Without it, both are absent. A tree that mixes images with and without
the capability, an unknown version, a capability outside `start`, a counter
outside a terminal, a missing or unadvertised counter, a counter below the
issue cost, or an aggregate overflow makes the measurement unavailable.

A successful aggregate adds the closed object

```json
"latency_bound": {"version": 1, "cycles_x16": 95940}
```

at the top level, on every process and on every selected image segment. The CLI
report adds `latency_bound_version: 1` when the bound was requested; a complete
measurement without the object (or with it when it was not requested) is
reported as unavailable (`client latency bound does not match the request`).
Combined instruction and REP counters are unchanged by the option.

## Evidence

`tests/latency_bound.py` builds four libc-free loop fixtures
(`tests/latency_bound/kernels.S`): a dependent multiply chain (6 cycles per
iteration), an L1 pointer chase (5), independent ALU work limited by issue
width (2), and a divide chain (21). Each has an entry block, a self-loop body
and an exit block whose bounds follow from the model by hand; two iteration
counts must reproduce the exact totals under the stock-core test client (release
and debug core). With `--client` and `--runner-bin`, the production client must
report the same instructions and bound through `durante run --latency-bound`,
and no bound without the flag. The code-replay fixtures from
`tests/jit_replay.S` must stay valid with unchanged instruction counts and a
bound of at least the issue cost.

In the exploratory work-feature study (`experiments/time_features`, a separate
change), this bound alone predicted 9 of 18 single-cost kernels within 20%
under leave-one-out fitting (median error 25%, against 82% for instructions
alone), all of them compute-bound; it failed where time was dominated by branch
mispredictions or memory. That was one noisy host and a generic table, so it
qualifies no model. A time model that uses this counter must be fitted and
held-out-qualified on the reference machine, with the bound recorded in the
dataset like any other counter.

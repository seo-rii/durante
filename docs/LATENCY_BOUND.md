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
- A memory source is ready 4 cycles after its address registers. Stores and
  `lea` wait for their address registers. A multi-byte `nop` ignores its operand.
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

## Charging

The bound is charged by the same flag-free inline block entry as the
instruction count, with or without the live clock (`--work-budget`). After
the count, the entry adds the block's constant, records the block's count and
bound for a replay of one of its writes, and keeps the counter's previous
value. A thread that the runtime relocates in the middle of an entry gets both
charges back, and re-entering the block charges them once. As for
instructions, the next entry latches a wrap of the 64-bit counter, and the
wrap makes the measurement unavailable. An unanalyzable block still takes a
callback that sets its error.

`--memory-operand-bytes` keeps the block-entry and write callbacks for every
counter, so it costs far more than the bound alone. (The 0.86% marginal cost
in the Rust comparison below was measured when the bound used those callbacks
too.) The inline entries and the callbacks count the same instructions and
bound; the equality checks under Evidence compare them.

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

## Memory operand bytes

Run `durante run --latency-bound --memory-operand-bytes -- PROGRAM` to
collect two inputs for a time model in one diagnostic pass. The same option
pair is available on `isolate`. The memory metric sums **nominal decoded
operand widths**. A 16-byte SIMD load contributes 16 bytes, its 16-byte
store contributes another 16, and an 8-byte read-modify-write contributes
16. Implicit stack operands contribute their width when exposed by the
decoder. The client computes block constants while translating code and
charges them at block entry in the same callback as the latency bound.

Address calculations, NOPs, prefetches and cache-line management instructions
contribute zero. Emulated instructions use the original application operands.
For example, a virtualized CPUID has no memory operand. Operands without a fixed
decoded width, including XSAVE's variable-sized area, also contribute zero.
Predicated and masked operations contribute their nominal width on block
entry. String operations contribute one decoded element width for each
source or destination memory operand. Use `rep_breakdown` separately for the
requested repetition count times element size. The memory metric describes
the program's decoded references. Cache-line fills, cache hits and misses,
DRAM traffic and actual active lanes are outside its scope.

An observed application signal or a thread stopped by a sibling before its
own exit syscall invalidates the byte measurement. On an internal replay the write checkpoint records the
byte widths in the unexecuted suffix. The client retains any completed
write and rolls back the remaining bytes. Overflow or an inconsistent
checkpoint invalidates the measurement.

## Evidence

The hand-counted fixture in `tests/latency_bound/memory_operand_bytes.S`
must report exactly 623 bytes. Across its block and store callbacks it
checks an application XMM register against a sentinel. The fixture covers
scalar, SIMD and stack operands, read-modify-write, address-only instructions,
fixed and variable save areas, and REP with zero and nonzero repeat counts.
The pinned Rust comparison measures the runtime cost of enabling this counter
and tests its contribution to estimated native CPU time.
[Measurements and archived receipts](GCE_RUST_OPERAND_BYTES.md)

`tests/latency_bound.py` builds five libc-free loop fixtures in
`tests/latency_bound/kernels.S`. They cover a dependent multiply chain (6 cycles per
iteration), an L1 pointer chase (5), independent ALU work limited by issue
width (2), a divide chain (21), and two dependent `lea` instructions (2).
Each has an entry block, a self-loop body
and an exit block whose bounds follow from the model by hand; two iteration
counts must reproduce the exact totals under the stock-core test client (release
and debug core). With `--client` and `--runner-bin`, the production client must
report the same instructions and bound through `durante run --latency-bound`,
and no bound without the flag. The code-replay fixtures from
`tests/jit_replay.S` must stay valid with unchanged instruction counts and a
bound of at least the issue cost.

The harness then compares the inline entries with the callbacks that
`--memory-operand-bytes` keeps. This covers one iteration count of each
kernel, the replay fixtures, the operand fixture and the ordinary, exec and
failed-exec REP lifecycle fixtures from `tests/rep_breakdown.S`. Each run is
repeated with the live clock. Every counted field must be identical: validity,
instructions, bound, REP counters, signals and every process and image row.
With `--engine-debug` the same comparison also runs on the debug core.
`--reference-client` adds a client built from another revision, for example
one whose bound always used callbacks. Every fixture must then match it too,
including the fork and vfork fixtures, which observe SIGCHLD, and a fault
handler that reads registers its block's entry spilled.
`tests/live_work_client.py --latency-bound` repeats the live page checks with
the bound, including a signal at each point of the boundary routine with
`--gdb`. `tests/jit_stress.py --latency-bound`, optionally with
`--work-budget`, runs the generated-code stress with it, in every mode.

In the exploratory work-feature study (`experiments/time_features`, a separate
change), a prototype that omitted `lea` address dependencies predicted 9 of 18 single-cost kernels within 20%
under leave-one-out fitting (median error 25%, against 82% for instructions
alone), all of them compute-bound; it failed where time was dominated by branch
mispredictions or memory. That was one noisy host and a generic table, so it
qualifies no model. A time model that uses this counter must be fitted and
held-out-qualified on the reference machine, with the bound recorded in the
dataset like any other counter.

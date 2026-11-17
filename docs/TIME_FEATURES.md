# Work features for a millisecond conversion

A judge that converts work counts to milliseconds needs a model whose error on
held-out programs is small. Aonohako's two-coefficient reference fit
(instructions plus REP bytes) failed that test: branch-heavy and REP-fill
programs were overestimated by 67-90% and sorting was underestimated by about
35%. `experiments/time_features.py` asks which additional work features would
be needed, using deterministic quantities that a DynamoRIO client can compute
on any host.

This is diagnostic evidence for model design. It qualifies no model, adds no
counter to the production client and does not make counts trustworthy.

## Method

The fixed corpus in `experiments/time_features/work.c` has 18 kernels, each
built to stress one cost: dependent and independent integer arithmetic,
predictable and random branches, sorting random and sorted input, pointer
chasing within L1, L2-sized and DRAM-sized cycles, sequential reads, dependent
and independent floating point, integer division, recursion, hash-table
inserts, matrix multiplication and byte loops. Hot loops make no library calls,
so host-specific library dispatch does not separate native timing from counts.
It is compiled with the judge's ordinary `-O2` and linked statically.

For each kernel at a fixed scale, `collect` records:

- native user+system CPU time: the median of 9 runs pinned to one logical CPU,
  measured with `wait4` per run;
- static instruction classes from an exploratory client: every application
  instruction, loads, stores, conditional branches, direct jumps/calls,
  indirect branches, multiplies, divides and floating-point operations, each
  counted per basic block at translation and charged at block entry;
- branch mispredictions from a deterministic gshare simulation (2^14 two-bit
  counters, 14 bits of global history) driven by every executed conditional
  branch;
- simulated L1 data and last-level misses from DynamoRIO's `drcachesim`
  (32 KiB 8-way L1, 8 MiB 16-way LL, 64-byte lines).

The branch and cache quantities are simulated, so they are deterministic for a
given execution and independent of the host processor. They are not the host's
actual events.

`fit` compares nonnegative linear models by leave-one-kernel-out relative
error: each kernel is predicted by a model fitted on the other 17, minimizing
squared relative error.

```sh
cmake -S experiments/time_features -B build-time-features -DCMAKE_BUILD_TYPE=Release \
  -DDynamoRIO_DIR="$PWD/toolkit/sdk/DynamoRIO-Linux-11.3.0-1/cmake"
cmake --build build-time-features
python3 experiments/time_features.py collect --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 \
  --cachesim-root /path/to/full/DynamoRIO-Linux-11.3.0-1 \
  --client build-time-features/libtime_features_client.so \
  --work build-time-features/time_features_work --output-dir .results/time-features --cpu 2
python3 experiments/time_features.py fit --input .results/time-features/dataset.json \
  --output .results/time-features/evaluation.json
```

`drcachesim` is not part of the SDK subset that `toolkit/fetch_sdk.py`
extracts; `--cachesim-root` must point to a complete extraction of the same
pinned release archive. Without it, the cache models are omitted. The feature
client runs on the stock core because it uses no Durante runtime ABI.

## Recorded result

RESULTS_PLACEHOLDER

## What this does and does not show

A model that reaches a usable error needs features the current client does not
count. Instruction classes are cheap: they are static per basic block and add
no instrumentation at run time beyond the existing block-entry charge.
Branch-predictor and cache simulation are dynamic, per branch and per memory
access, and cost far more. Running the simulation inside the judge would slow
measured runs well beyond the current instrumentation overhead.

The evidence is one host, one compiler, one process at a time, no system calls
of note and simulated microarchitecture. Simulated miss counts approximate a
generic cache, not the reference machine's prefetchers, replacement policy or
memory latency. Multithreaded, JIT and allocation-heavy workloads are outside
this corpus. Any production model would still need held-out qualification on
the reference machine, as in aonohako's reference collection, and would still
be application-mutable.

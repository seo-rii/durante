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

The CPP20 branch and sort replay uses Aonohako’s original executables and inputs
to test which counters distinguish two observed native time differences.
[Replay method and results](REFERENCE_FEATURES.md)

The CPU-pinned Rust study tests scalar multiplies and loop recurrence against
matched native controls from Compute Engine. [Measurements and replay](GCE_RUST_INSTRUCTION_MIX.md)

A second comparison reuses those Rust executables to measure the cost and
predictive accuracy of the production latency and memory operand counters.
[Pinned Rust memory operand study](GCE_RUST_OPERAND_BYTES.md)

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
  (32 KiB 8-way L1, 8 MiB 16-way LL, 64-byte lines, default next-line
  prefetcher);
- a static latency bound per basic block, described with the results.

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
mkdir -p .results
python3 experiments/time_features.py collect --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 \
  --cachesim-root /path/to/full/DynamoRIO-Linux-11.3.0-1 \
  --client build-time-features/libtime_features_client.so \
  --work build-time-features/time_features_work --output-dir .results/time-features --cpu 2
python3 experiments/time_features.py fit --estimator min --input .results/time-features/dataset.json \
  --output .results/time-features/evaluation.json
```

`drcachesim` is not part of the SDK subset that `toolkit/fetch_sdk.py`
extracts; `--cachesim-root` must point to a complete extraction of the same
pinned release archive. Without it, the cache models are omitted. The feature
client runs on the stock core because it uses no Durante runtime ABI.

## Recorded result

Local run on 2026-11-17: Intel Xeon @ 2.10 GHz cloud VM, Linux
6.18.44-fc-v553, GCC 13.3.0, stock DynamoRIO 11.3.0-1 release core, one pinned
logical CPU, 9 native runs per kernel. Static classes, branch simulation and
cache simulation came from the first pass; the latency features (see below)
came from a second deterministic feature pass on the same binary and inputs,
whose static counts agreed with the first within 23 instructions per kernel.
Dataset SHA-256: `6291a67c686e4c0855ae07cf66edb99d8189e7dda481c7c17b52d9c627986a0b`
(work binary `f65f1ff3...5f373269`).

The client used for this dataset omitted the base and index register
dependencies of x86 `LEA`. The M4 and M5 rows and their per-kernel errors
below use those historical counts. The corrected client waits for both
registers and charges one ALU cycle for `LEA`. Its regression fixture measures
two recurrence cycles per iteration for two chained `LEA` instructions.
New M4 and M5 accuracy figures require recollecting those features and
refitting on the same workloads. The historical table uses `--estimator min`.

**Native timing on this host was itself unstable.** Pinned, deterministic runs
of the same kernel varied by a median of 23% (max/min over 9 runs) and up to
2.5x (`branch_pred`: 88-224 ms), presumably from shared-host interference and
frequency changes. Interference only adds time, so the fits below use each
kernel's minimum; the median gives the same conclusions. This is the problem a
work-based judge exists to avoid, and it also means that this host cannot
qualify a model: the reference timings need a quiet, dedicated machine.

Leave-one-kernel-out absolute relative error (minimum native time):

| Model | Features | Median | Max | Within 20% | In-sample max |
|---|---|---:|---:|---:|---:|
| M0 | instructions | 81.7% | 142.9% | 3/18 | 98.2% |
| M1 | static instruction classes | 80.3% | 1014% | 3/18 | 90.0% |
| M2 | M1 + simulated mispredictions | 79.8% | 1126% | 4/18 | 90.8% |
| M3 | M2 + simulated L1D/LL misses | 75.2% | 676% | 4/18 | 87.5% |
| M4 | static latency bound | 25.4% | 97.6% | 9/18 | 97.6% |
| M5 | M4 + mispredictions + L1D/LL misses | 22.1% | 153.0% | 8/18 | 70.0% |

Counting more instruction classes does not help: even unconstrained least
squares cannot fit M3 in-sample (78% maximum error). Same-opcode work has very
different cost depending on dependencies: a dependent FP multiply-add chain
(`fp_chain`) and independent FP operations (`fp_ilp`), or pointer chasing in L1
(`chase_l1`) and sequential reads (`seq_read`), have similar class mixes but
different speeds.

M4 adds one static feature per basic block, computed once at translation with
a deliberately generic latency table (ALU 1, multiply 3, divide 20, FP 4, FP
divide 14 cycles; loads ready 4 cycles after their address registers; register
and flag dependencies only): the larger of a 4-wide issue bound and, for a
self-looping block, its loop-carried recurrence per iteration. It costs no more
at run time than the existing block-entry charge. On its own it predicts 9 of
18 kernels within 20%, all of them compute-bound (integer and FP chains and
independent work, division, L1 pointer chasing, byte loops, recursion, and
matrix multiplication at -19%). The generic 4-wide issue bound overestimates the
predictable-branch loop by 96%: the host completed its 8.25 instructions per
iteration in about 0.28 ns, more than 4 per cycle at any plausible clock (for
example through compare-and-branch fusion), so a model needs a front-end term
rather than a fixed width. M4 fails where time is dominated by mispredictions or memory
(random branches -89%, random sort -84%, DRAM pointer chasing -98%, hash
inserts -95%, sequential reads -76%).

M5's simulated penalties fix the branch- and L2-bound kernels (random branches
-17%, random sort -0.5%, L2 pointer chasing +2%) but cannot fit both sequential
and random memory traffic with one miss weight: sequential reads are
overestimated by 153% and DRAM pointer chasing underestimated by 74%. The
simulator's next-line prefetcher hid only 74 of 3.67 million sequential L1D
misses, so every streamed line counts as a demand last-level miss, whereas
hardware stream prefetchers hide most of that latency. A usable memory term
would have to distinguish access patterns (sequential or strided versus
random), not count raw simulated misses.

## What this does and does not show

The direction that helps is a dependency-aware static cost per basic block,
not more instruction classes: it is cheap (computed once at translation and
charged at block entry like the instruction count) and explains the
compute-bound kernels. Branch-predictor and cache simulation are dynamic, per
branch and per memory access, and cost far more; running them inside the judge
would slow measured runs well beyond the current instrumentation overhead, and
the memory term would still need an access-pattern model rather than raw miss
counts. A plausible design is therefore a static latency bound for every block
plus a small number of cheaper dynamic counters chosen for the reference
workloads, qualified on held-out composite programs on a quiet machine.

The evidence is one host, one compiler, one process at a time, no system calls
of note and simulated microarchitecture. Simulated miss counts approximate a
generic cache, not the reference machine's prefetchers, replacement policy or
memory latency. Multithreaded, JIT and allocation-heavy workloads are outside
this corpus. Any production model would still need held-out qualification on
the reference machine, as in aonohako's reference collection, and would still
be application-mutable.

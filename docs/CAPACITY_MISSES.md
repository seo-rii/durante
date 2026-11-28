# Cache-capacity and dependent-load misses as time features

Instruction and REP counts, the static latency bound and simulated L1D misses
cannot tell working sets apart once they all miss a 32 KiB L1D
([crossed memory study](CPP20_CROSSED_MEMORY.md)). This study adds exact misses
at larger cache capacities and misses of dependent loads to the research cache
probe, then tests on programs no model has seen whether they improve CPU-time
predictions. Neither round met its stated decision rule. Each plan and its results first
appeared together in the repository. The commit history provides no independent
public timestamp for a plan frozen before measurement. The added
counters move errors in the right direction for memory-latency-bound programs,
and the remaining dominant error is cross-block pointer chasing.

## Counters

`experiments/l1d_probe` builds the client with `DURANTE_L1D_PROBE`. With
`-probe_l1d -probe_outer` it also prints a `PROBE_OUTER` line:

- **Misses at 256 KiB, 2 MiB and 32 MiB**, from fully associative LRU caches fed
  by the lines that miss the L1D model, in miss order. One LRU stack with a
  boundary node per capacity gives every capacity exactly in O(levels) per miss
  (`outer.h`). Being fully associative, the counts depend only on the sequence of
  distinct lines, not on how virtual addresses map to sets.
- **Stream misses.** The plan classifies a miss as a stream miss if its line is
  within two lines of any of the last 16 miss addresses at that capacity. Both
  archived rounds instead tracked up to 16 streams. A matching miss replaced
  the previous address of its stream and promoted the stream. This can change
  both `outer_stream` and the derived random-miss counts.
- **Dependent misses.** The round-2 probe tracked addresses derived from an
  earlier load within a block, including loops that branch back to the same
  block. Its handling of x86 `LEA` omitted dependencies carried through the
  base or index of an effective address. Consequently the archived dependent
  counts can miss even some within-block chains.

`cache_test.c` checks every count, the whole LRU order and every boundary
against a literal stack-distance reference, alone and behind the L1D model,
including the bulk REP STOS and MOVS paths. That check found that a backward
REP STOS whose first element straddles two lines touched them in the opposite
order from per-element replay; the fast path now matches. On the kernels, 89% of
a pointer chase's L1D misses are dependent and none of an independent random
read's.

The probe also marked block starts with `drmgr_is_first_instr()`, which stopped
working when the client began handling blocks without drmgr: per-block read
references were never charged and the probe's assembly control had failed
since. The client now passes the block start, and all three probe tests pass.

## Round 1 (decision rule failed)

[predeclared.txt](../experiments/capacity_misses/predeclared.txt) fitted four
models on 14 synthetic kernels and scored 22 held-out programs (the crossed
memory, memory-cost and branch-load corpora and the seven contest problems).
Every model failed: no calibration kernel varied REP bytes, so the REP weight
acted as an intercept, and held-out programs with large copies were mispredicted
by up to 10^8 percent. Native spreads were 15-70% while other jobs ran on the
guest, leaving one stable held-out case.

After seeing that data, a leave-one-out fit over the 22 real programs (not
predeclared) put 6-8 of 22 within 20% with instructions and REP bytes and 12-14
with capacity misses added; median error fell from 28.7% to about 17.5%. The
largest misses were a linked list (-84%) and a latency-bound gcd loop (-62%).
This motivated the dependent-load counter and the latency bound in round 2. The
round-1 cases were then used only for calibration.

## Round 2 (decision rule failed)

[predeclared-v2.txt](../experiments/capacity_misses/predeclared-v2.txt)
calibrated on the 36 round-1 cases and scored 49 contest solution variants that
no model had seen ([corpus](JUDGE_DISAGREEMENT.md)), 42 of them within about 15x
of the intended solution. Native time is the median of 15 interleaved rounds on
one pinned CPU of a 4-vCPU Intel Xeon guest. Round 1 recorded two probe runs
for each of its 36 cases. Every pair agreed on all recorded counters. Round 2
recorded one probe run for each of its 85 cases, so counter repeatability is
unknown for all 85. The archived round-2 JSON records their `probe_repeatable` field as
`true` because the old collector compared each sample with itself. The analysis
now derives `probe_repeatable` from the sample list and reports `null` when
fewer than two samples are available.

| Model | Features | Within 20% (all 42) | Stable within 20% | Median error | Worst |
|---|---|---:|---:|---:|---:|
| M0 | instructions, REP bytes | 9 | 1/5 | 38.7% | 125.8% |
| ML | latency bound, REP bytes | 3 | 0/5 | 60.1% | 91.5% |
| M1 | M0 + L1D misses | 9 | 1/5 | 42.5% | 116.9% |
| M2 | M1 + random 2/32 MiB, stream 2 MiB | 11 | 1/5 | 44.5% | 99.2% |
| M4 | M2 + dependent L1D, dependent 2 MiB | 11 | 1/5 | 39.7% | 123.6% |
| ML4 | ML + the M4 memory features | 7 | 0/5 | 49.8% | 99.7% |

Only 5 of the 42 variants met the 20% stability gate (median spread 30.8%; other
jobs ran on the guest's other CPUs during the collection), so neither primary
candidate could meet the rule, and neither did. Grouped by where the variants
spend their time, the median signed error was:

| Variants | M0 | M2 | M4 |
|---|---:|---:|---:|
| Pointer chasing, hashing, cache-hostile stride (13) | -67% | -60% | -44% |
| Compute, sequential, allocation (23) | -18% | -17% | -7% |
| I/O and system calls (6) | -5% | -19% | -12% |

Every model underpredicts `std::map`, `std::set`, `unordered_map`, `multimap`,
order-statistics trees and linked lists by 40-89%. Their dependent loads cross
blocks (each tree level compares and branches), which the intra-block
dependency rule does not see; only the multimap DFS, whose chain stays in a
loop block, improved strongly (-89% to -43% with M4). Synced iostream was
overpredicted by up to 125%: many cheap instructions. Variants that make one
`write(2)` per answer are underpredicted because the counters do not see kernel
time.

## Conclusions and next steps

- On this guest, none of these feature sets is accurate enough for a deciding
  clock. The capacity and dependent counters reduce the systematic
  underestimate of memory-latency-bound programs, but not enough.
- Cross-block load chains are the next feature to try: track loaded values
  across block boundaries (a per-register taint carried in the thread state), or
  sample load-to-use distances.
- Kernel time needs a separate input: counts of system calls and the bytes they
  move.
- The research probe is slow: a median 29x native CPU in round 1 (up to 91x)
  and 65x in round 2 (up to 283x, with the latency bound's callbacks added). A
  production version would need sampling.
- Repeat round 2 on a dedicated host: the noise here leaves most cases outside
  the stability gate.

## Replay and collect

The published scores describe the archived stream and dependency counters.
Changes to the research probe apply to new collections only. No paired dataset
from the corrected counter implementation is available here. Replay the
historical scores directly from their recorded measurements.

```sh
python3 -B experiments/capacity_misses/analyze.py experiments/capacity_misses/results/v1/results.json
python3 -B experiments/capacity_misses/analyze_v2.py experiments/capacity_misses/results/v2/results.json
```

The following commands collect a new dataset with two probe measurements per case.

```sh
cmake -S experiments/l1d_probe -B build/l1d -DCMAKE_BUILD_TYPE=Release \
  -DDynamoRIO_DIR="$sdk/cmake" -DDURANTE_TEST_DRRUN="$sdk/bin64/drrun"
cmake --build build/l1d && ctest --test-dir build/l1d
python3 -B experiments/capacity_misses/collect.py --sdk "$sdk" --client build/l1d/libdurante_l1d.so \
  --plan experiments/capacity_misses/plan-v2.json --predeclared experiments/capacity_misses/predeclared-v2.txt \
  --latency-bound --rounds 15 --probe-runs 2 --output-dir NEW_DIR
python3 -B experiments/capacity_misses/analyze_v2.py NEW_DIR/results.json
```

The recorded runs are in `experiments/capacity_misses/results/` (`v1` with its
scores, `v2` with a summary; `analyze_v2.py` regenerates the full v2 scores).

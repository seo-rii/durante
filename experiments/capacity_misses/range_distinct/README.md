# Range-distinct queries on two pinned Milan boots

Adding 3.3 ns per simulated branch miss to ML4 corrected two false timeouts on
this previously unused problem and left two. A larger, independently frozen
penalty corrected all four original false timeouts but introduced one false
accept. Both penalties put all four median time ratios within ±20% of native.
The same verdicts occurred on each boot and after pooling their ranges.

## What was held out

The fixed array has 32,768 entries drawn from 256 values. Each of its 32,768
inclusive queries asks how many distinct values occur in an interval. The
reference sorts queries by their right endpoint and maintains a Fenwick tree
of the last position of each value. One alternative uses Mo's algorithm. The
other combines 256-bit masks for complete 128-element blocks with scans of
the two partial blocks. All three emit one answer per original query ordinal.

Half the queries have a span of at most 95 indices and half use two independent
endpoints. The second input reorders the same queries within 512-query
windows, alternately sorting the windows ascending and descending. Both orders
contain 32,713 distinct query pairs, including 55 repeated occurrences. The
independent oracle searches a sorted list of positions for each value. The
two ordered answer digests differ, and all 156 completed processes produced the
expected digest for their own input. [Solvers](queries.cpp) ·
[Input generator and oracle](inputs.py) · [ELF and input identities](frozen.json)

The earlier affordable-item and inversion-count panels yielded a retrospective
interval of 3.07892785–3.54116450 ns per gshare miss. This third problem
tests exactly 3.3 ns/miss without refitting. The other three candidates were
also fixed before measuring this problem. ML4 uses eight nonnegative
features with the original Dijkstra weights. The 6.92658480142496 ns penalty
comes from the earlier gshare fit. The two-term score adds 700 times the
dependent L1D miss count to the static latency bound in sixteenths of a cycle.
Each score is divided by the same model's reference score on the same input.
[Prior two-problem transfer](../order_statistics_transfer/README.md) ·
[Frozen two-term candidate](https://github.com/seo-rii/durante/pull/125) ·
[Preregistered protocol at d740a9e](https://github.com/seo-rii/durante/blob/d740a9eddf8d18bc95d78ab13e4a4e569fae61c1/experiments/capacity_misses/range_distinct/protocol.json)

## Accuracy at registered deadlines

A cell accepts when every observed submission/reference pair fits its
inclusive deadline, times out when every pair exceeds it, and is mixed
otherwise. The 20 cells are the two alternatives, two input orders and five
factors (1, 1.25, 1.5, 2, 3). Every native and model cell was decisive on this
panel. Time-ratio error uses median submission time divided by median reference
time. The error statistics below pool both boots. Repeated deadlines on a
single solver pair and reordering the same queries do not add independent
problems. [Individual boots, pooled verdicts and signed errors](results.json)

| Frozen model | Agrees with native | False accept | False timeout | Time ratios within ±20% | Median absolute error | Worst absolute error |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ML4 | 16/20 | 0 | 4 | 2/4 | 20.45% | 34.88% |
| ML4 + 6.9266 ns/miss | 19/20 | 1 | 0 | 4/4 | 5.56% | 9.23% |
| Latency + 700 × dependent misses | 16/20 | 0 | 4 | 2/4 | 21.48% | 36.18% |
| ML4 + 3.3 ns/miss | 18/20 | 0 | 2 | 4/4 | 10.81% | 16.55% |

| Query order and alternative | Native median ratio (full range) | ML4 | +6.9266 | Two-term | +3.3 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Shuffled, Mo | 1.414 (1.355–1.466) | 1.416 | 1.284 | 1.426 | 1.338 |
| Shuffled, bitset | 1.168 (1.117–1.194) | 1.575 | 1.204 | 1.590 | 1.357 |
| Window-sorted, Mo | 1.340 (1.303–1.432) | 1.428 | 1.243 | 1.438 | 1.320 |
| Window-sorted, bitset | 1.170 (1.134–1.213) | 1.571 | 1.215 | 1.587 | 1.364 |

The 3.3 ns penalty fixed the bitset at 1.5× in both orders and still timed it
out incorrectly at 1.25×. The 6.9266 ns penalty accepted all four of those
bitset cells. It also accepted the window-sorted Mo solver at 1.25×, where
even its best observed native ratio was 1.303×.

The preregistered three-model unanimity rule covered 15/20 cells (75% of
all cells and of native-decisive cells), with zero false accepts and zero
false timeouts on those 15. The models disagreed on the remaining five,
including the window-sorted Mo false accept. The 3.3 ns estimate was evaluated
separately from these votes. The existing set-Dijkstra study gives a unanimous false accept at 2× on
its first boot. The GCE native range was 2.101–2.449× on that boot and
1.944–3.288× on the second. Combining the matched local gshare readings on
the registered ELFs with the earlier cache counters gives ratios of 1.523×
for ML4, 1.516× with the larger miss penalty, 1.082× for the two-term model
and 1.519× with 3.3 ns/miss. Those earlier observations are outside this
panel's 20 cells.
[Set-Dijkstra workload and bound](https://github.com/seo-rii/durante/pull/117) ·
[Branch-counter transfer](https://github.com/seo-rii/durante/pull/119)

## Measurement cost and reproduction

Each independent AMD Milan n2d-standard-4 boot used one thread per core and
pinned all processes to CPU 1. The second boot reversed the trial sequence.
Every ELF/input pair had seven native and two each of original full-cache,
flat full-cache and branch diagnostics per boot. Both CPUs recorded zero
steal ticks. Original and flat cache fields, shared work counters and
gshare counts matched their repeat readings. Pooled medians for the native
solvers spanned 9.26–13.10 ms. Diagnostic CPU costs relative to the same
native solver were 117.27–152.24× for original full cache, 62.84–85.18× for
flat cache, 73.08–99.04× for branch prediction and 135.92–183.65× to collect
both flat and branch in separate processes. These ratios include process
startup. The four native timing ratios and the deadline replay describe
completed runs on these binaries.

Across both boots, the 84 native executions consumed 0.924 CPU seconds. The
24 executions of each diagnostic consumed 35.585 seconds for original cache,
20.102 for flat cache and 22.122 for branch prediction. Each collection,
timed from provisioning request through resource deletion verification, took
about 3 minutes 15 seconds. The guest and disk both returned HTTP 404 after
each run and both temporary keys were removed. [Cloud receipts](evidence/cloud.json)

Run the replay and regressions from this directory with Python 3.11+ and a
C++20 compiler. The raw archives include ordered trial receipts, CPU exec
traces, process records, cache reports and branch counts for each boot.

```sh
python3 -B replay.py --verify results.json
python3 -B -m unittest -v test_inputs test_replay
```

To build a fresh payload from the registered tools directory used by the
earlier inversion study, use a new destination and the compiler version
recorded in the plan. The preparation script checks inherited tool digests and
executes all six program/input combinations against the oracle before packaging.

```sh
python3 -B prepare.py --registered-tools "$INVERSION_PAYLOAD" --output "$NEW_PAYLOAD"
```

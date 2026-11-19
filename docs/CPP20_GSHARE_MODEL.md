# Predicting CPP20 native CPU time with simulated branch misses

The search/window comparison identified a branch counter that could explain one
observed ordering. This experiment tests whether that counter improves time
estimates on two programs that were held out from model selection.
[Search/window comparison](CPP20_PATTERN_REPLAY.md)

The v8 Cloud Run study tests a load/branch interaction against an additive control on three new programs. [V8 prediction and live boundary results](CPP20_V8_CLOCK.md)

## Protocol

Both models fit the same 15 family/scale medians from integer, REP copy, sort,
binary search and rolling window. The baseline uses instructions, REP bytes
and load instructions. The candidate adds misses from Durante's
`gshare-14bit-pcshift1-v1` software predictor. The coefficients are
nonnegative, the intercept is zero, and each training median has equal weight
in squared relative error. The evaluator checks rank before fitting. Branch
and REP fill are historical diagnostics. Neither contributes to the fit.

The prospective families are a stable merge of two sorted streams and a
read-only open-address hash-table lookup. Each has three fixed input sizes
and three repetitions. The predeclared accuracy threshold requires every
one of the 18 predictions to be within ±20% of its case's native CPU median.
The six native readings behind each median come from running the same static
program before and after each pair of instrumented executions. Each pair
contains a production-meter run and a production-meter-plus-probe run.
Their CPU-time ratio measures probe cost. Every execution must return the
expected stdout. [Frozen inputs, stdout and artifact hashes](../experiments/branch_corpus.json)

An independent oracle checks the generated data as well as the checksums. An
initial merge generator gave every corresponding pair the same key. Its
incomplete collection was discarded before fitting. In the corrected inputs,
28,575 pairs have the smaller key on the left, 28,733 on the right, and 8,228
are tied. All six prospective outputs were frozen before the corrected
collection. [Fixture checks](../tests/test_gshare_heldout.py)

## Observed accuracy and cost

On this shared reference host, the baseline met the ±20% threshold for 0 of
18 prospective predictions. Adding gshare brought 6 of 18 within the threshold.
The largest absolute errors were 67.6% for the baseline and 34.4% for the
candidate. All three counter readings agreed at each prospective input size.
The six passes come from two distinct merge inputs. Each of the three hash
inputs was underestimated by more than 20%.

| Held-out program | Scale | Native CPU median (ms) | Baseline error | Gshare estimate (ms) | Gshare error |
|---|---:|---:|---:|---:|---:|
| Sorted merge | 1 | 9.072 | -43.3% | 8.119 | -10.5% |
| Sorted merge | 2 | 14.777 | -31.4% | 16.109 | +9.0% |
| Sorted merge | 4 | 25.910 | -22.3% | 32.089 | +23.9% |
| Hash lookup | 1 | 16.816 | -67.6% | 11.035 | -34.4% |
| Hash lookup | 2 | 29.895 | -64.1% | 21.716 | -27.4% |
| Hash lookup | 4 | 54.950 | -61.3% | 43.077 | -21.6% |

Across the 81 pairs, the median CPU cost of running the meter and exploratory
client together was 1.552 times the cost of the production meter alone
(range 1.185–1.843). This includes all instrumentation in the exploratory client.
On the two historical diagnostic families the baseline met the threshold for
9 of 18 and the candidate for 0 of 18. The measurements were taken on a shared
AMD EPYC 7B12 host. The reference CPU range for
the smallest hash input was 16.184–28.708 ms, compared with a prediction of
11.035 ms. These shared-host observations cover static CPP20 test programs.
The candidate failed its prospective accuracy criterion. Aonohako continues to
use the native CPU measurement for grading this path.
[Paired observations and provenance](../experiments/results/cpp20-gshare-model.json)

To reproduce the table from the published observations, run the evaluator
with a new output path.

```sh
python3 experiments/gshare_model.py --input experiments/results/cpp20-gshare-model.json --output evaluation.json
```

## Can different weights fit the observed corpus?

After scoring the held-out inputs, we checked whether changing the four
nonnegative weights could bring every observed case within ±20%. Two training
cases give a direct counterexample. Their counter values were identical in
each of the three repetitions.

| Scale 4 program | Instructions | REP bytes | Load instructions | Gshare misses |
|---|---:|---:|---:|---:|
| Integer compute | 671,238,769 | 3,624 | 22,490 | 1,089 |
| Rolling window | 872,908,987 | 3,624 | 134,240,236 | 1,059 |

For every column the integer count is at most 1.03 times the window count.
Any nonnegative weighted sum preserves that upper bound. Even the fastest
observed integer execution (170.607 ms) requires a prediction of at least
136.486 ms. Even the slowest window execution (105.126 ms) permits at most
126.152 ms, implying an integer prediction below 129.936 ms. Those intervals
cannot overlap. This counterexample applies to this model family and corpus.
A qualified timing model needs additional information about the work the CPU
performs. [Executable check against the raw observations](../tests/test_gshare_model.py)

## Adding the production latency bound

The production meter also recorded its v1 static latency bound during every
gshare run. It assigns each basic block an issue-width or loop-recurrence
cost, in sixteenths of a modeled cycle. We recovered this fifth feature
after the study and checked it against the exploratory client's simultaneous
bound, the frozen four counters and all 81 production receipts. The integer
and window scale-4 runs had bounds of 8,590,535,988 and 3,491,636,912 units
respectively, a ratio of 2.460. This is high enough to resolve the ordering
used in the previous four-counter counterexample. [Latency-bound definition](LATENCY_BOUND.md)

A different three-case comparison rules out ±20% accuracy on all 27 case
medians even with this extra feature. For each of the five counters the
large search performed at most two fifths of the large sort plus three fifths
of the medium merge. The exact inequality holds across all three repetitions.

| Case | Instructions | REP bytes | Load instructions | Gshare misses | Bound ×16 | Observed native CPU range (ms) |
|---|---:|---:|---:|---:|---:|---:|
| Binary search, scale 4 | 152,978,668 | 3,624 | 14,178,048 | 8,408,317 | 611,915,516 | 104.990–108.551 |
| Sort, scale 4 | 373,668,023 | 3,624 | 67,622,014 | 19,557,115 | 1,494,673,064 | 142.830–145.977 |
| Sorted merge, scale 2 | 74,466,475 | 3,624 | 8,410,820 | 1,100,078 | 297,866,824 | 14.430–15.714 |

Any linear estimator with nonnegative counter weights inherits this
inequality, including one with a common intercept since the mixture
weights sum to one. Even the fastest observed search needs a prediction
of at least 83.992 ms. At the slowest observed sort and merge times, their
weighted maximum passing prediction is only
`1.2 × (0.4 × 145.977 + 0.6 × 15.714) = 81.38304 ms`.
The corresponding limits at the native medians are 84.658 and 79.6386 ms.
No choice of these five weights can put the three cases inside the specified
intervals on this dataset.

This retrospective check uses the original shared-host measurements, including
the same programs and native timings. It adds no qualification for new
submissions. To test a further feature on these cases, first check whether it
breaks the measured search/sort/merge inequality. Any new accuracy claim
still needs independent inputs and a controlled reference machine.

The public archive contains the original collection and the byte-for-byte
production record and probe output from every dual-client execution.
Filesystem ownership and timestamps were removed when packaging this subset.
The verifier checks the archive and collection digests, reconstructs all 81
bounds with the reference collector and emits the integer inequalities.
It also accepts the original archived collection via `--archive`.
[Public raw records](../experiments/results/cpp20-gshare-probe-records.tar.gz)

```sh
python3 experiments/joint_latency_screen.py
```

## Nineteen counters from the same executions

The paired probe and production receipts contain 19 distinct counter fields
for each of the 81 repetitions. The verifier reads all 27 family/scale cases
and computes a second comparison under `full_counter_screen`. Its inputs come
from the following fields.

| Source | Fields |
|---|---|
| Paired probe | Instructions, loads, stores, conditional branches, direct jumps/calls, indirect branches, multiplies, divides, floating-point operations and simulated gshare misses |
| Paired probe, in sixteenths of a modeled cycle | Critical path, loop recurrence and the static latency bound |
| Production meter | Total REP bytes and entries, MOVS bytes and entries, STOS bytes and entries |

The simultaneous production totals independently check the probe's instruction
count and static latency bound. The verifier also checks the original published
load and branch counts, and uses the production collector to validate each REP
partition. Total and per-operation REP counters are related. The comparison
allows each of them its own nonnegative weight. [Probe implementation](../experiments/time_features/featclient.c) · [Production record validation](../collect.py)

For every one of the 19 fields, the largest search count across repetitions
satisfies the following inequality with the smallest recorded count for each
program on the right.

```text
60 × search(scale 4) ≤ 23 × sort(scale 4) + 8 × branch(scale 2) + 29 × merge(scale 1)
```

A weighted sum with nonnegative counter weights preserves the inequality.
Any common intercept cancels because 23 + 8 + 29 = 60. The most favorable
native controls for a ±20% fit are the fastest search (104.990 ms) and the
slowest sort, branch and merge (145.977, 68.845 and 9.451 ms). The smallest
acceptable search prediction is 83.992 ms. The largest acceptable weighted
prediction on the right is
`1.2 × (23 × 145.977 + 8 × 68.845 + 29 × 9.451) / 60 = 83.6462 ms`.
The intervals miss by 0.3458 ms. The verifier compares both bounds in integer
nanoseconds scaled by 300 and reports the slack for every counter.

This retrospective comparison rules out a common affine model with nonnegative
counter weights on these archived observations. The underlying timings remain
those of the original shared reference host. [Executable comparison](../experiments/joint_latency_screen.py)

## Reproduce

Use the Linux x86-64 worker, pinned CPP20 compiler image, Aonohako checkout,
judge bundle and DynamoRIO SDK described in the previous experiment. Select
one available logical CPU. The collector requires support for per-process
ASLR disabling and records the CPU, kernel and tool hashes. The native clock
is direct-child user plus system CPU, including the shared execution prefix.
The instrumented paths use the production meter's fixed x86-64-v2 CPUID
profile. The native paths run on the host CPU. [Build prerequisites](CPP20_PATTERN_REPLAY.md#reproduce)

```sh
RUN=$(mktemp -d)
python3 experiments/collect_branch_model.py prepare --aonohako "$AONOHAKO" --output "$RUN/fixtures"
cmake -S experiments/time_features -B "$RUN/build" -DCMAKE_BUILD_TYPE=Release -DDynamoRIO_DIR="$SDK/cmake"
cmake --build "$RUN/build" --target time_features_client
python3 experiments/collect_branch_model.py collect --fixtures "$RUN/fixtures" \
    --bundle "$BUNDLE" --probe "$RUN/build/libtime_features_client.so" --cpu "$CPU" --output "$RUN/observations"
python3 experiments/gshare_model.py --input "$RUN/observations/collection.json" --output "$RUN/evaluation.json"
```

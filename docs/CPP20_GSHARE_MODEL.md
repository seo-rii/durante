# Predicting CPP20 native CPU time with simulated branch misses

The search/window comparison identified a branch counter that could explain one
observed ordering. This experiment tests whether that counter improves time
estimates on two programs that were held out from model selection.
[Search/window comparison](CPP20_PATTERN_REPLAY.md)

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

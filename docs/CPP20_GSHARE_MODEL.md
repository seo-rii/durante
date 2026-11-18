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

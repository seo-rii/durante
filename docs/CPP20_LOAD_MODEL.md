# Does counting load instructions improve CPP20 timing predictions?

In the branch/sort replay, the total number of instructions that read memory
distinguished both pairs of programs. This experiment tests whether that count
also improves predictions on two further algorithms. [Earlier replay](REFERENCE_FEATURES.md)

## Study design

The baseline predicts native CPU nanoseconds from instructions and REP bytes.
The candidate adds one nonnegative coefficient for load instructions. Both
fits have zero intercept. Each of nine training cases contributes one row
using its median counters and the median of six native runs. The objective is
the sum of squared relative timing errors. Coefficients are frozen before
scoring either validation group.

| Use | Algorithms | Reason |
|---|---|---|
| Training | integer, REP copy, sort | Sort was studied in the earlier replay and provides independent variation in loads. |
| Historical diagnostics | branch, REP fill | Both algorithms informed earlier model design. |
| Prospective evaluation | lower-bound queries, rolling window sum | Both programs, input scales and independent stdout oracles were fixed before collecting timings. |

Every algorithm runs at scales 1, 2 and 4. The prospective accuracy threshold
requires every one of the 18 metered predictions to fall within ±20% of its
case's native CPU median. Each model is fitted on the same training data and
tested against the same reference times. The original Aonohako split assigned
sort to validation, so these results belong to a new experiment.

For each case and repetition, a native run precedes and follows a pair of
Durante runs. Both use the actual judge client and patched x86-64-v2 runtime
configuration. One run also loads the experimental client, which charges
memory-reading instructions at basic-block entry. Its dynamic instruction
total must equal Durante's in the same process. REP MOVS counts as one load
instruction on a block entry, and its byte demand is a separate production
counter. The experiment accepts only single-threaded, single-image, uninterrupted
runs with identical expected stdout. Native execution uses the host CPU.
Linux wait4 measures direct-child user and system CPU time. The measurements
include launch costs and use a different timing boundary from Aonohako's sandbox.

A fixed seed shuffles the order of cases each round. Each case reverses the
probe order in the next round, with initial order balanced across cases.
The paired CPU times with and without the probe measure the incremental cost
of loading a second client and running its extra basic-block callback.

The load classification follows DynamoRIO 11.3 `instr_reads_memory` on the
original application instruction. It includes read-modify-write, prefetch
and predicated memory-read instructions. A predicate can suppress the actual
access. Stores that only write, LEA and memory-operand NOPs contribute zero.

## Recorded result

Both models missed the ±20% target on **all 18 prospective predictions**.
Both also missed all 18 historical diagnostic predictions. The positive fitted
load coefficient slightly reduced the search underestimate and made the
streaming window overestimate larger. Signed errors below are identical
across the three counter repetitions in each prospective case.

| Prospective workload | Scale | Native CPU median (ms) | Baseline error | With load count |
|---|---:|---:|---:|---:|
| `lower_bound` | 1 | 33.8320 | -64.76% | -61.31% |
| `lower_bound` | 2 | 55.7055 | -58.27% | -53.96% |
| `lower_bound` | 4 | 108.2435 | -57.60% | -53.10% |
| `window_sum` | 1 | 22.7450 | +188.36% | +265.82% |
| `window_sum` | 2 | 40.5125 | +223.42% | +310.41% |
| `window_sum` | 4 | 77.9335 | +236.06% | +326.51% |

One pair rules out fixing these observations by reweighting the same three
counters. The large search performed 152,978,603 instructions and 14,178,037
load instructions. The small window performed 218,597,563 instructions and
33,576,940 load instructions. Both had 3,624 REP bytes. Every nonnegative
weighted sum of these counters therefore predicts the search at or below the
window. Even the fastest observed search took 102.772 ms and the slowest
window took 29.048 ms. With ±20% tolerance those observations require the
search prediction to exceed 82.217 ms and the window prediction to stay at
or below 34.858 ms.

The paired probe-to-meter CPU time ratio was **1.587×** at the median across
63 pairs (range 1.149× to 2.525×). This includes startup, translation, the
additional block callback and writing the probe report. An implementation
integrated into the existing meter would need its own overhead measurement.

Measurements ran on one pinned logical CPU on a shared AMD EPYC 7B12 host.
Across the six native runs per case, 17 of 21 cases exceeded a 10% spread
relative to their median. For both models, every prospective search estimate
was below 80% of its fastest native observation and every window estimate
exceeded 120% of its slowest. The result supports investigating branch
predictability and memory access patterns on a dedicated reference host
before changing the judge. The two prospective programs can now serve as
diagnostics for that investigation. Reserve fresh algorithms for its final
accuracy test.

The protocol, fixtures and oracle were frozen in commit `595f619` before the
collection. The patched core and production client SHA-256 values match the
Aonohako CPP20 reference installation. All 252 executions matched their
independent stdout expectations. The published collection SHA-256 is
`dd62734d0690c2b0fbfd8df09fb483f9834fc50b0bf5ee16e5f22b8a3a29d2d8`.
[Raw observations and tool identities](../experiments/results/cpp20-load-model.json)

## Reproduce

The recorded observation file contains all 63 paired measurements and can
be scored using only Python's standard library. Give the evaluator a fresh
file path for its report.

```sh
python3 experiments/load_model.py --input experiments/results/cpp20-load-model.json \
    --output evaluation.json
```

For recollection, use Linux x86-64, Docker, the local compiler image ID
recorded in the corpus, an Aonohako source checkout with the pinned commit,
and a matching Durante build. The corpus lists every compiler input and oracle.
Preparation rebuilds all seven programs with the archived flags and checks
each source and ELF digest before collection. [Corpus manifest](../experiments/load_corpus.json)

Set `SDK` to an absolute path to the prepared development SDK, `BUNDLE` to an
operator-verified Aonohako runtime directory containing
`dynamorio/bin64/drrun` and `lib/libdurante.so`, and `CPU` to an allowed logical
CPU. If working from a Durante checkout, an equivalent local layout is a fresh
directory with `dynamorio` linked to the patched SDK and `lib/libdurante.so`
linked to the corresponding release client. The recorded hashes identify
the actual tools used. [Judge installation layout](https://github.com/seo-rii/aonohako/blob/fa4b95e744b10e3442f62bcbfa9a772882b63212/docs/durante.md)

```sh
RUN=$(mktemp -d)
cmake -S experiments/load_probe -B "$RUN/build" -DCMAKE_BUILD_TYPE=Release \
    -DDynamoRIO_DIR="$SDK/cmake"
cmake --build "$RUN/build" --target load_probe
python3 experiments/collect_load_model.py prepare --aonohako "$AONOHAKO" \
    --output "$RUN/fixtures"
python3 experiments/collect_load_model.py collect --fixtures "$RUN/fixtures" \
    --bundle "$BUNDLE" --probe "$RUN/build/libload_probe.so" --cpu "$CPU" \
    --output "$RUN/results"
python3 experiments/load_model.py --input "$RUN/results/collection.json" \
    --output "$RUN/evaluation.json"
```

# When can an IOI Scrivener deadline skip profiling?

An exact interval let the clock skip 14 of 24 branch profiles on each of two CPU-pinned GCE guests. All 24 physical deadline decisions per guest matched the full clock. With both modes given the same paid reference measurements, CPU spent on these decisions fell **58.17% and 58.36%**. The experiment covers six new input orders and two previously calibrated solver binaries. [Primary measurements](../experiments/results/gce-scrivener-lazy/metrics.json) · [Independent repeat](../experiments/results/gce-scrivener-lazy-repeat/metrics.json)

## Paid decisions and common calibration

| Per guest, 24 calls in each mode | First guest | Second guest |
|---|---:|---:|
| Eager scans / branch profiles | 24 / 24 | 24 / 24 |
| Demand-driven scans / branch profiles | 12 / 10 | 12 / 10 |
| Verdict from anchors alone / after scan / after branch profile | 12 / 2 / 10 | 12 / 2 / 10 |
| Eager child + dispatcher CPU | 12.825 s | 12.875 s |
| Demand-driven child + dispatcher CPU | 5.365 s | 5.361 s |
| Eager / demand-driven elapsed | 12.830 / 5.364 s | 12.877 / 5.363 s |
| Common calibration child CPU | 3.802 s | 3.802 s |

Charging the common calibration child CPU once to each mode reduces the savings over these 24 calls to 44.87% and 45.06%. The collector measured dispatcher CPU during candidate decisions only. The 108 native validation runs per boot provide the reference labels. Both guests recorded zero CPU steal ticks, and their CPU-1 traces each account for all 220 measured child processes. [First guest and cleanup](../experiments/results/gce-scrivener-lazy/collection.json) · [Second guest and cleanup](../experiments/results/gce-scrivener-lazy-repeat/collection.json)

## Accuracy against native time

The full clock and the demand-driven clock also agreed for every one of the 144 distinct input/solver/deadline combinations on each boot, for each of the two profiling rounds. The table compares their common result with the median of nine native runs per input/solver pair. A false timeout means the clock timed out a submission the indicated native reference passed.

| Measure | First guest | Second guest |
|---|---:|---:|
| Agreement with native median at the 12 assigned deadlines | 9/12 | 10/12 |
| Agreement with native median over the 144-deadline grid | 132/144 | 135/144 |
| False timeout / false pass against native majority on the grid | 11 / 1 | 9 / 0 |
| False timeout / false pass where all nine native runs agreed | 8 / 0 | 6 / 0 |
| Grid decisions with mixed native votes | 7 | 7 |
| Full clock median / worst absolute timing error, 12 pairs | 4.31% / 28.06% | 4.49% / 27.90% |
| Anchor midpoint median / worst absolute timing error, 12 pairs | 3.61% / 28.82% | 3.59% / 27.76% |
| Full clock / midpoint within ±20%, of 12 pairs | 10 / 11 | 10 / 11 |

The largest full-clock error on both guests came from the compact solver with 96-query tiles shuffled within 40 lanes. Its native medians were 50.599 and 50.550 ms, and all 18 native runs finished below 60 ms. The full clock predicted 64.799 and 64.653 ms. The pure branch and pure scan forecasts span **64.217–65.380 ms** on the first guest and **64.069–65.238 ms** on the second. Every weighted average of these two normalized features with nonnegative weights summing to one therefore produces the same false timeout. Improving this case requires changing how the features map to time or measuring another feature. The journals retain the inputs and solver identities for that comparison. [First journal](../experiments/results/gce-scrivener-lazy/output.tar.gz) · [Second journal](../experiments/results/gce-scrivener-lazy-repeat/output.tar.gz)

The binary-lifting solver exposes a different limit. Both the 20-lane ascending and 40-lane descending inputs reach the upper clamp on normalized branch misses. Their time estimates differ by less than 0.6 microseconds on each guest, while their native medians differ by 1.242 and 1.974 ms.

## How the deadline is decided

The previous Scrivener clock estimates CPU time for a known solver using two native reference inputs. It measures the median native time for an ascending and a globally shuffled query order. It measures two features at both anchors and on the candidate input. One feature counts disruptive page revisits in the input order. The other counts simulated conditional branch mispredictions in the compiled solver. The clock normalizes each feature against its two anchors, clamps it to the unit interval, averages the two fractions and interpolates the native reference times. Equal feature anchors contribute one half. The clock rounds its final estimate up to an integer nanosecond. [Original model](GCE_SCRIVENER_STREAMS.md)

Before measuring a feature, its normalized fraction can be bounded by zero and one. Substituting the endpoints of that range gives inclusive lower and upper bounds on the final estimate. A lower bound strictly above the limit guarantees a model timeout. An upper bound at or below the limit guarantees a model pass. Otherwise the decision runs the input scanner, narrows the bounds, and requests the branch profiler only if the interval still straddles the limit. An early decision returns the verdict and its interval. Callers needing a single estimated duration can request the full clock. [Implementation](../experiments/scrivener_lazy/model.py)

## Experiment

Six new orders interleave 20 or 80 ascending lanes, interleave 40 descending lanes, shuffle tiles of 96 or 384 queries within 40 lanes, or bit-reverse ranks inside 256-query tiles. Each preserves the same document, multiset of 180,000 lookups and character oracle. Each order runs on the compact jump-tree and binary-lifting solvers from the previous experiment. A hash of each input name assigns a fixed deadline of 30, 40, 45, 50, 60 or 75 ms before measurement. [Inputs](../experiments/scrivener_lazy/corpus.py) · [Frozen budgets and protocol](../experiments/scrivener_lazy/plan.json)

Each of two independent GCE Milan boots pins the parent and its children to CPU 1, with one hardware thread per core. The two decision modes share one anchor calibration per boot. Each then runs twice on each held-out input/solver pair, in opposite order across the two rounds. Every requested scanner or branch profiler is launched afresh. The dispatcher seals all 48 decisions and their measurement records before timing the held-out programs directly nine times each. A kernel execution trace records the processes actually launched. [Collector](../experiments/scrivener_lazy/bench.py) · [Independent replay](../experiments/scrivener_lazy/verify.py)

Decision CPU includes the dispatcher's own thread CPU time and the user plus system CPU of its scanner and profiler children. Elapsed time covers the same calls. The separate calibration includes 36 native runs, two input scans and four branch profiles per guest. Contestant execution and output checking still have to be performed when judging a submission. The twelve-by-twelve grid of solver/input pairs and deadlines uses the recorded eager features to compare possible decisions offline. It supplies no additional timing samples for the routing algorithm.

## Reproduce the results

From the repository root, replay both guest archives and compare the resulting summaries byte for byte.

```sh
tmp=$(mktemp -d)
for suffix in '' '-repeat'; do
    result="experiments/results/gce-scrivener-lazy${suffix}"
    dir="$tmp/boot${suffix}"
    mkdir "$dir"
    tar xzf "$result/output.tar.gz" -C "$dir"
    python3 -B experiments/scrivener_lazy/verify.py "$dir/output" --output "$dir/metrics.json"
    cmp "$dir/metrics.json" "$result/metrics.json"
done
python3 -B -m unittest discover -s tests -p 'test_scrivener_lazy*.py' -v
```

The guest builder takes the registered Durante v12 installation and pinned Debian Cachegrind files. It rebuilds and verifies both static solver binaries and the original stream scanner. [Payload builder](../experiments/scrivener_lazy/prepare.py)

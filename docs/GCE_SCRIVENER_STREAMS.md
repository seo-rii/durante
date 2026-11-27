# Timing sequential streams in IOI Scrivener

The revised page statistic corrected the previous 65 ms false timeout on both guests. On **twelve fresh input/solver pairs**, combining it with branch mispredictions cut worst-case absolute error from **47.75% to 24.15%** and **48.59% to 21.30%**. Against the previous branch/page model the new estimate corrected 12 and 15 unanimous false timeouts without introducing a new unanimous error. Both boots passed the registered accuracy and deadline criteria. These criteria compare two models on the fresh inputs. [Primary results](../experiments/results/gce-scrivener-streams/metrics.json) · [Independent repeat](../experiments/results/gce-scrivener-streams-repeat/metrics.json)

## Fresh-input accuracy and deadlines

Each cell gives the first and second guest results. Eight of 144 budget comparisons on the first guest and seven on the second had mixed native votes. The false-pass and false-timeout columns count cases where all nine native runs agreed. [First journal](../experiments/results/gce-scrivener-streams/output.tar.gz) · [Second journal](../experiments/results/gce-scrivener-streams-repeat/output.tar.gz)

| Estimate | Median absolute error | Worst absolute error | Within ±20%, of 12 | Majority agreement, of 144 | False pass | False timeout |
|---|---:|---:|---:|---:|---:|---:|
| Mean of the two native anchors | 3.87% / 3.96% | 24.81% / 25.66% | 11 / 11 | 136 / 138 | 0 / 0 | 5 / 5 |
| Ordinary Durante work | 15.41% / 15.93% | 34.03% / 34.77% | 7 / 7 | 128 / 126 | 12 / 13 | 0 / 0 |
| Adjacent query distance | 11.88% / 10.73% | 38.61% / 39.52% | 7 / 8 | 126 / 128 | 0 / 0 | 14 / 14 |
| Original page misses | 15.01% / 14.61% | 51.32% / 52.13% | 7 / 7 | 123 / 118 | 1 / 1 | 17 / 22 |
| Page misses with revisit distance | 13.57% / 13.57% | 27.74% / 24.79% | 7 / 7 | 131 / 128 | 5 / 6 | 4 / 5 |
| Simulated branch misses | 14.65% / 14.29% | 44.19% / 45.05% | 8 / 7 | 125 / 123 | 0 / 0 | 15 / 19 |
| Branch + original page misses | 14.33% / 14.32% | 47.75% / 48.59% | 7 / 8 | 124 / 121 | 0 / 0 | 17 / 20 |
| Branch + page misses with revisit distance | 4.96% / 3.96% | 24.15% / 21.30% | 10 / 11 | 136 / 136 | 0 / 0 | 5 / 5 |

Averaging the two native anchors requires no additional profiling per input. This midpoint covered 11/12 fresh pairs within ±20% on both guests and matched 136/144 and 138/144 majority decisions. The combined model covered 10/12 and 11/12, with 136/144 majority agreements on each guest. It improved the worst error relative to the mean, especially on the bit-reversed tiles, but these data give little reason to pay for branch simulation on every judged submission. The mean still requires two measured anchor inputs per known executable.

The input scan itself cost a median 7.23 / 7.21 ms per input, shared by both solvers. Branch simulation cost 526.66 / 530.15 ms per executable/input, ordinary Durante profiling took 180.36 / 182.67 ms, and direct native execution took 42.24 / 42.65 ms. Using the revised scan alone reduced worst error versus the original scan on both guests, but introduced four and five unanimous false passes. The scan-only model failed its deadline criterion.

## The 65 ms control and the remaining error

For the compact solver on the historical 32-stream control, the original LRU missed all 180,000 queries. Only 414 of these met the revised criterion. The old combined forecast was 66.86 / 66.25 ms and the new forecast was **50.13 / 49.89 ms**. The native medians were **45.71 / 45.32 ms** and all 18 native runs passed at 65 ms. This input informed the new design and is excluded from the fresh-input table.

The worst fresh error on both guests was the compact solver with shuffled 64-query tiles inside 48 lanes. Its native median was 50.34 / 51.09 ms and the revised forecast was 62.49 / 61.98 ms. This order and the descending 48-lane order had identical aggregate adjacent-distance statistics and 180,000 original page misses apiece. The revised scan counted 132,345 and 430 qualifying misses, respectively. That distinction moved the predictions in the observed direction but exaggerated the time difference. Within each guest all repeated ordinary work totals and simulated branch counts were identical for each executable/input pair. All twelve fresh pairs on both guests had a nine-run native range smaller than 10% of their median. Both guests recorded zero CPU steal ticks. [First guest and cleanup](../experiments/results/gce-scrivener-streams/collection.json) · [Second guest and cleanup](../experiments/results/gce-scrivener-streams-repeat/collection.json)

## What was measured

In the previous experiment a 16-entry page counter missed on every one of 180,000 queries interleaved across 32 sorted streams. For the compact-tree solver the combined page and branch model predicted a timeout at 65 ms on the second guest. Every native run passed. We retained this input and its exact historical executable as a separate regression control. [Previous result](GCE_SCRIVENER_PAGES.md)

The revised scan remembers the last queried character position in each logical page of 512 positions. It counts a miss in the original 16-entry LRU if the page has never been queried or if the new position is more than eight characters from its previous query. Every query updates the remembered position, including LRU hits. The scan stores one additional integer per page (352 integers on these inputs). It measures a property of the query sequence; the experiments collected no hardware cache counters. [Scanner](../experiments/scrivener_streams/scan.cpp)

Six fresh inputs keep the document, the multiset of lookup positions and the character oracle fixed. They interleave 24 or 64 ascending streams, interleave 48 descending streams, shuffle 64- or 512-query tiles inside each of 48 interleaved streams, or bit-reverse query ranks inside successive 512-query tiles. Both the compact jump-tree and binary-lifting solutions run each order. [Input generator](../experiments/scrivener_streams/corpus.py)

The scan-only estimate interpolates between the measured ascending and globally shuffled native medians for the same solver on the same guest. The combined estimate averages two fractions, one from the scan and one from simulated conditional branch mispredictions. We clamp each fraction to the anchor interval and round the final prediction up to a nanosecond. The previous page-only and branch/page estimates use the same anchors. The study also reports the midpoint, ordinary Durante v12 work, adjacent query distance and branch-only estimates. [Exact formulas](../experiments/scrivener_streams/score.py) · [Registration](../experiments/scrivener_streams/plan.json)

On each of two separate AMD Milan GCE guests, a cpuset and scheduler affinity pin the measured processes to CPU 1 with one hardware thread per core. After 135 process receipts, the collector saves all 112 forecasts and a hash of the preceding journal. Only then does it time the control and the fresh inputs. Each native reference is the median of nine measurements of child user plus system CPU time. All eight methods use the same native references. The six fresh orders supply 12 input/solver pairs and 144 deadline comparisons per guest, covering 30–85 ms in 5 ms steps. Equality passes. Deadline outcomes are projected from completed runs. [Collector and registration](../experiments/scrivener_streams/bench.py)

## Reproduce the results

From the repository root, verify both captures against the published summaries.

```sh
tmp=$(mktemp -d)
for suffix in '' '-repeat'; do
    result="experiments/results/gce-scrivener-streams${suffix}"
    dir="$tmp/boot${suffix}"
    mkdir "$dir"
    tar xzf "$result/output.tar.gz" -C "$dir"
    python3 -B experiments/scrivener_streams/verify.py "$dir/output" --output "$dir/metrics.json"
    cmp "$dir/metrics.json" "$result/metrics.json"
done
python3 -B -m unittest discover -s tests -p 'test_scrivener_streams*.py' -v
```

The payload builder takes the registered Aonohako v12 installation, the pinned Debian 12 Cachegrind tool directory and a fresh target directory. It verifies the tools and rebuilds both static C++ solvers and the scanner using GCC 12.2. [Builder](../experiments/scrivener_streams/prepare.py) · [Runtime provenance](https://github.com/seo-rii/aonohako/pull/158)

```sh
python3 -B experiments/scrivener_streams/prepare.py \
    --runtime "$V12_INSTALL" --cachegrind "$CACHEGRIND_FILES" --output "$NEW_PAYLOAD"
```

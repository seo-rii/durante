# Predicting Scrivener CPU time from query pages and branches

Adding logical page misses to simulated conditional branch mispredictions lowered the median absolute error on ten held-out input/solver pairs from **10.63% to 4.78%** on one pinned Milan guest and from **10.62% to 5.01%** on a second. The worst error also fell on both, meeting the registered accuracy criterion. The deadline criterion passed on the first guest. On the second, the false-pass count stayed at one and the combined estimate introduced a false timeout at 65 ms. [First guest metrics](../experiments/results/gce-scrivener-pages/metrics.json) · [Second guest metrics](../experiments/results/gce-scrivener-pages-repeat/metrics.json)

## Held-out results

Values separated by a slash give the first and second independent guest results. Each guest contributed 120 input/solver/budget comparisons. The last two columns count errors where all nine native executions agreed. Nine boundaries on the first guest and six on the second had mixed native outcomes. [First guest raw receipts](../experiments/results/gce-scrivener-pages/output.tar.gz) · [Second guest raw receipts](../experiments/results/gce-scrivener-pages-repeat/output.tar.gz)

| Estimate | Median absolute error | Worst absolute error | Within ±20%, out of 10 | False pass | False timeout |
|---|---:|---:|---:|---:|---:|
| Mean of two native anchors | 13.78% / 14.02% | 46.71% / 46.76% | 6 / 7 | 4 / 5 | 8 / 9 |
| Ordinary Durante work scaled from ascending | 9.66% / 8.81% | 52.70% / 52.13% | 8 / 8 | 10 / 10 | 0 / 0 |
| Adjacent query distance | 10.28% / 10.13% | 84.45% / 79.34% | 6 / 6 | 0 / 1 | 15 / 17 |
| Logical page misses | 6.57% / 7.03% | 54.28% / 54.37% | 9 / 9 | 1 / 2 | 5 / 5 |
| Simulated branch misses | 10.63% / 10.62% | 57.62% / 53.07% | 7 / 7 | 1 / 1 | 12 / 14 |
| Mean of branch and page fractions | 4.78% / 5.01% | 48.75% / 48.98% | 8 / 9 | 0 / 1 | 6 / 9 |

The combined estimate agreed with 109/120 and 110/120 native majority verdicts, up from 102/120 and 105/120 for branch alone. On the compact solver's bit-reversed input, every native run exceeded 70 ms on both guests. The combined forecast crossed that boundary on the first guest (70.94 ms) and missed it on the second (68.55 ms). On the second guest the 32-stream input passed in all nine native runs (43.51–46.36 ms). Its branch estimate was 64.63 ms and its combined estimate was 67.06 ms. That introduced the 65 ms false timeout even as five fewer unanimous false timeouts occurred overall. [Per-boundary changes](../experiments/results/gce-scrivener-pages-repeat/metrics.json)

## Where the page count helps and fails

Bit-reversed and alternating-extreme queries had nearly the same adjacent-distance statistic (2.969 million and 2.977 million), yet the logical page counter recorded 178,973 and 352 misses. On the compact tree the native medians were 75.14 / 73.56 ms for bit reversal and 39.00 / 38.75 ms for alternating extremes. This contrast is visible in the page count and persists on the second guest.

The 16-stream and 32-stream inputs expose the specific limit of the 16-entry LRU. They produced 366 and 180,000 logical page misses. Compact-tree medians changed from 43.89 to 46.63 ms on the first guest and from 43.70 to 45.01 ms on the second. The combined estimates jumped from 52.36 to 69.35 ms and from 51.05 to 67.06 ms respectively. The geometry of the input scanner exaggerates the cost of switching among 32 sorted streams. These runs did not measure hardware cache events. [Scanner](../experiments/scrivener_pages/scan.cpp) · [Repeat profiles](../experiments/results/gce-scrivener-pages-repeat/metrics.json)

The shared input scan cost a median **6.28 / 6.30 ms** per execution. Median per-executable branch-only profiling cost **526.32 / 522.33 ms**, ordinary Durante profiling **179.83 / 176.33 ms**, and native execution **40.19 / 38.88 ms**. Using only the cheap page scan covered 9/10 held-out pairs within ±20% on each guest, with worst errors above 54%. The combined model pays for both the scan and the branch profile. Branch counts matched across both repetitions and both guests on every pair. Ordinary work differed by at most 16 counts between repetitions. Both guests recorded zero CPU steal ticks across their 203 scheduled processes. These results cover two known static executables, one problem and one CPU family. [First collection](../experiments/results/gce-scrivener-pages/collection.json) · [Second collection](../experiments/results/gce-scrivener-pages-repeat/collection.json)

## Inputs and timing

The compact jump-tree solver and the binary-lifting solver each build the same 180,000-character document and print the same multiset of 180,000 positions. Only query order changes. The five new orders visit bit-reversed ranks, alternate the first and last remaining ranks, interleave 16 or 32 sorted streams, or permute 512-query blocks while preserving their inner order. A character lookup in the original document supplies the output oracle. [Input generator](../experiments/scrivener_pages/corpus.py) · [Scrivener order controls](GCE_SCRIVENER_ORDER.md)

Each solver has two paid native calibration inputs on each guest, one ascending and one globally shuffled. A standalone input scan counts misses in a 16-entry LRU over logical pages of 512 character positions. It also sums the bit lengths of the distances between consecutive queries. Both solvers share that scan. Branch-only Cachegrind 3.19.0 estimates conditional branch mispredictions separately for each executable. The ordinary Durante v12 arm counts instructions and requested REP bytes under the x86-64-v2 profile. [Input scanner](../experiments/scrivener_pages/scan.cpp) · [Collector](../experiments/scrivener_pages/bench.py)

Each of two independent AMD Milan N2D guests ran the same frozen payload using one hardware thread per core, a CPU 1 cpuset and CPU 1 scheduler affinity. The reference is the median of nine direct child user-plus-system CPU times for the same static ELF and stdin. The collector sealed all 60 forecasts on each guest after the first 113 process records and before the 90 held-out native runs. The fixed budgets span 30–85 ms in 5 ms steps. Deadline comparisons use estimates from completed runs and pass at equality. [Registration](../experiments/scrivener_pages/plan.json) · [Forecast formulas](../experiments/scrivener_pages/score.py)

## Replay

From the repository root, unpack and verify each original guest capture.

```sh
tmp=$(mktemp -d)
for suffix in '' '-repeat'; do
    result="experiments/results/gce-scrivener-pages${suffix}"
    output="$tmp/boot${suffix}"
    mkdir "$output"
    tar xzf "$result/output.tar.gz" -C "$output"
    python3 -B experiments/scrivener_pages/verify.py "$output/output" --output "$output/metrics.json"
    cmp "$output/metrics.json" "$result/metrics.json"
done
python3 -B -m unittest discover -s tests -p 'test_scrivener_pages*.py' -v
```

To rebuild the payload, use the registered Aonohako v12 worker installation with `dynamorio/` and `lib/libdurante.so`, the three pinned Debian Valgrind tool files, and GCC 12.2 with static C++ libraries. The builder checks the executable and runtime hashes and produces the guest CPU-affinity and trace wrapper. [Payload builder](../experiments/scrivener_pages/prepare.py) · [Aonohako v12 installation and pins](https://github.com/seo-rii/aonohako/pull/158)

```sh
python3 -B experiments/scrivener_pages/prepare.py \
    --runtime "$V12_INSTALL" --cachegrind "$CACHEGRIND_FILES" --output "$NEW_PAYLOAD"
```

# Calibrating a uniform Rust binary search across table sizes

**The span correction passed all five fresh Rust inputs and one of two repeats.** Across four instrumented runs per input, the largest error on the fresh inputs was 15.86%, compared with two of five passes using the v9001 baseline. The repeated 100,003-key input was overestimated by 30.72% despite having a stable native reference and passing the distribution check. The added probe CPU was 0.40% across 48 pairs. [Signed errors, fit and gate outcomes](../experiments/results/gce-search-footprint/evaluation.json) · [Archived dataset](../experiments/results/gce-search-footprint/dataset.json.gz)

The query-order study found two workloads where the same locality correction moved the prediction in opposite directions relative to the target. It estimated 32.812 ms for a 37.645 ms Rust search on a 1 MiB table, then 74.415 ms for a 38.794 ms search on a smaller table. Both used the same executable and a pseudorandom query generator. The footprint study measures whether active array size resolves this discrepancy when the executable and query distribution are held fixed. [Earlier experiment](GCE_SEARCH_ORDER.md)

## Inputs and estimator

All twelve cases use one archived, optimized Rust executable, including the original insertion-index checksum. The new probe records the smallest and largest terminal element index at the same sampled visits as the query-order probe, including an unpaired last visit. The inline countdown and sampling cadence are identical. The observed span is four times the inclusive difference between the two element indices. All calibration and inference use this measured span. The fixture independently reconstructs the actual input, checksum, bounds and complete and sampled width histograms. [Fixture definitions](../experiments/results/gce-search-footprint/fixtures.json) · [Probe source and live checks](../experiments/search_footprint/probe)

Five calibration cases span 16 KiB, 256 KiB, 512 KiB, 1 MiB and 8 MiB. At each size, we subtract the median paired v9001 estimate from the median native CPU time and divide by measured searches. Between adjacent calibration points, the estimator linearly interpolates this signed residual in bytes and adds it to the clock once per query. It checks the ELF digest, the terminal comparison offset, sample count, observed span and agreement with an independent-uniform endpoint distribution. This distribution check compares the observed and expected cumulative probabilities of the sampled index widths at every width. The maximum allowed difference is 1/12. The calibrated range allows 5% endpoint displacement. [Model](../experiments/search_footprint/model.py) · [Acquisition and scoring freeze](https://github.com/seo-rii/durante/commit/85d44683458ec7dbfc0e7169448dd095fc01e447)

The two repeated inputs allow a direct comparison with the preceding VM. The five new inputs use different seeds and near-nominal table sizes of 96 KiB, 384 KiB, 768 KiB, 2 MiB and 4 MiB. Every diagnostic estimate is compared to the median of eight native user-plus-system CPU observations. An input passes if all four estimates are eligible and within ±20%. A separate sensitivity check removes one calibration size at a time. [Evaluation plan](../experiments/search_footprint/evaluation-plan.json)

## Prospective observations

| Input | Native median | v9001 | Previous width correction | Span correction | Span error |
| --- | ---: | ---: | ---: | ---: | ---: |
| Repeat, original 1 MiB | 35.356 ms | 22.039 ms | 32.813 ms | 33.627 ms | −4.89% |
| Repeat, 100,003 keys | 38.111 ms | 52.019 ms | 74.415 ms | 49.818 ms | +30.72% |
| New, about 96 KiB | 45.166 ms | 73.751 ms | 97.303 ms | 49.727 ms | +10.10% |
| New, about 384 KiB | 52.153 ms | 62.410 ms | 89.058 ms | 59.342 ms | +13.79% |
| New, about 768 KiB | 61.768 ms | 54.977 ms | 80.886 ms | 71.562 ms | +15.86% |
| New, about 2 MiB | 81.283 ms | 48.747 ms | 73.336 ms | 81.397 ms | +0.14% |
| New, about 4 MiB | 115.329 ms | 48.802 ms | 76.155 ms | 100.396 ms | −12.95% |

For the failed repeat, measured endpoints spanned 399,856 bytes. The new 384 KiB input covered 392,940 bytes. Both passed the distribution check, yet native CPU averaged 29.08 versus 33.16 ns per query. The repeat's eight native observations ranged from 37.507 to 38.592 ms, leaving the model outside ±20% even against its slowest native run. This limits what the span and endpoint distribution establish. Removing the 1 MiB calibration point also produced a −20.99% error on that size. Removing the 256 or 512 KiB point still gave a passing prediction there; omitting an endpoint put it outside the calibrated range. [Per-case measurements and sensitivity](../experiments/results/gce-search-footprint/evaluation.json)

All 48 instrumented observations matched the independent extrema, input checksums and width histograms. Both the old and new probes count only sparse pairs, and the footprint measurement adds two comparisons inside the sampled callback. The total child CPU was 52.939 s for v9001 and 53.152 s with the footprint probe, a pooled +0.40% and a median paired +0.19%. Every native run exceeded the 20 ms duration threshold. The 8 MiB calibration point (24.20%), the original repeat (15.56%) and the fresh 2 MiB case (11.21%) exceeded the 10% spread threshold; four of five fresh inputs passed both native quality gates and the timing target. [Per-process receipts](../experiments/results/gce-search-footprint/receipts.tar.xz)

On the first attempt, a private packaging script used the quartet count as the expected Git commit hash length. It stopped before workload transfer or measurement. The unused VM, its boot disk and its temporary key were removed. The study then ran on a fresh Milan guest using the previously published source and protocol. During the 113-second successful collection, the scheduler trace placed all 192 measured processes and all 96 same-PID loader executions on CPU 1. CPU 0 observed 452 times. The trace reported zero overruns, and guest steal on CPU 1 stayed at zero. [Platform, placement and both teardown receipts](../experiments/results/gce-search-footprint/platform.json)

## Why two similar spans may differ

After scoring the frozen model, we replayed the actual indexed table reads for the 98,317-key and 100,003-key inputs through software LRU caches. For a 32 KiB eight-way cache with 64-byte lines, the 98,317-key search incurred 7.08–9.12 simulated misses per query across the 16 possible element alignments. The 100,003-key search incurred 4.88–4.89. With the array aligned to a line and an equally sized fully associative cache, the two results were 4.847 and 4.881 misses per query. The separation in this simulation therefore depends on how addresses map to cache sets. The slower native workload is also the one with more simulated eight-way misses. Hardware cache replacement, prefetch, instruction fetches and interference were not measured by this replay. [Simulator and reproduction](../experiments/search_cache_replay) · [All alignments and capacity controls](../experiments/results/gce-search-footprint/cache-alignment.json)

## Reproduction and use

The offline predictor reads the original ELF, the canonical version-9001 per-process record directory and the schema-2 footprint report from the same process. It reports the baseline and calibrated estimates in nanoseconds. Outside its measured scope it gives a reason and an empty calibrated estimate. The profile is specific to this executable, its terminal comparison and the reference guest. It does not qualify arbitrary Rust submissions for Aonohako verdicts.

```sh
python3 -m unittest discover -s tests -p 'test_search_footprint.py'
python3 experiments/search_footprint/estimate.py \
  --elf /path/to/Main --records /path/to/sampled/records --sample /path/to/sampled/sample.json
```

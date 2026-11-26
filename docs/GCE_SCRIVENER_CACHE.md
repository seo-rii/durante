# Can simulated cache misses predict Scrivener CPU time?

The simulated last-level read-miss count (DLmr) beat the registered two-endpoint midpoint on both guests. Median absolute error fell from **12.81% to 5.41%** and **14.07% to 6.22%** across six held-out pairs per guest. The gain over the existing ordinary Durante estimate was **0.006 percentage points** on each guest, with identical fixed-limit verdicts. Simulating cache and branch events together cost **6.36× and 6.29×** ordinary profiling. These results support using the simulator to study errors. They give little reason to add this cache model to every judged run. [Guest 1 metrics](../experiments/results/gce-scrivener-cache/metrics.json) · [Guest 2 metrics](../experiments/results/gce-scrivener-cache-repeat/metrics.json)

## Experiment

The previous study found that shuffling the same print queries almost doubled the compact Scrivener solver's CPU time on AMD Milan. Its Durante instruction count, REP bytes, static latency bound and memory operand bytes barely changed. This experiment asks whether cache or branch simulation can predict CPU time on previously untimed query streams. [Previous measurements](GCE_SCRIVENER_ORDER.md) · [Registered experiment](../experiments/scrivener_cache/plan.json)

Both solvers build the same 180,000-character document and print the same multiset of 180,000 positions. The calibration streams order those queries by ascending position or shuffle the entire sequence. Two fresh streams shuffle consecutive groups of 32 or 4,096 sorted queries. A third visits groups of 256 in reverse group order and keeps the queries inside each group ascending. All five streams have 113,621 distinct positions and take exactly 5,733,748 compact-tree or 1,538,346 binary-lifting ancestor steps. A separate character oracle checks each output. [Input generator](../experiments/scrivener_cache/corpus.py) · [Input tests](../tests/test_scrivener_cache_cases.py)

Each guest ran both hash-verified static Scrivener executables under Durante #97's released ordinary instruction/REP client and under Debian's Cachegrind 3.19.0 with branch simulation. The simulated cache has separate 32 KiB L1 instruction and data caches and a unified 512 KiB last level, all 8-way with 64-byte lines. DLmr counts simulated data reads that missed both cache levels. Bcm counts simulated conditional branch mispredictions. Both totals cover the whole process, including input parsing. The simulator uses a fixed geometry and branch predictor. These runs collected no hardware events that could identify the source of a Milan stall. [Cachegrind manual](https://valgrind.org/docs/manual/cg-manual.html) · [Collector](../experiments/scrivener_cache/bench.py)

The ordinary estimate scales the ascending native median by the ratio of Durante combined instruction/REP work. Four other methods use two native medians from the same executable and guest. The midpoint averages them. The other three interpolate between them using adjacent query distance, DLmr or Bcm. A feature outside the interval takes the closer endpoint in feature space, and an equal pair of endpoint features selects the midpoint. The registered primary comparison is DLmr against the midpoint, which uses the same native calibration. Cachegrind adds one instrumented execution per input. [Exact forecasting and scoring rules](../experiments/scrivener_cache/score.py)

On each guest the collector profiled all ten input/executable pairs and ran nine native repetitions of the four calibration pairs. It then wrote all thirty held-out forecasts, with a hash covering the first 56 process records, before launching any of the 54 held-out native processes. The reference time is the median of nine child user-plus-system CPU observations per pair. Verdict comparisons use the ten registered budgets from 30 through 75 ms in 5 ms steps. A reference is unanimous when all nine observations land on the same side of the budget. A run exactly at the budget passes. [Collection schedule](../experiments/scrivener_cache/bench.py) · [Raw replay](../experiments/scrivener_cache/verify.py)

## Accuracy and verdicts

Values are shown as first guest / second guest. Signed error is (prediction − native median) / native median. Each guest contributed 60 comparisons, of which 59 had unanimous native verdicts. Each false TLE or false pass below counts a disagreement with all nine native observations.

| Forecast | Median absolute error | Worst error | Within 20% (of 6) | Majority matches (of 60) | Unanimous false TLE | Unanimous false pass |
|---|---:|---:|---:|---:|---:|---:|
| Ordinary Durante | 5.41% / 6.22% | 21.93% / 21.40% | 5 / 5 | 57 / 57 | 0 / 0 | 2 / 2 |
| Endpoint midpoint | 12.81% / 14.07% | 43.43% / 44.20% | 4 / 4 | 51 / 52 | 9 / 7 | 0 / 1 |
| Query distance | 1.20% / 1.83% | 22.82% / 25.65% | 5 / 5 | 57 / 57 | 3 / 3 | 0 / 0 |
| Simulated read misses (DLmr) | 5.41% / 6.22% | 21.60% / 21.05% | 5 / 5 | 57 / 57 | 0 / 0 | 2 / 2 |
| Simulated branch misses (Bcm) | 4.03% / 3.51% | 16.71% / 19.23% | 6 / 6 | 58 / 58 | 2 / 2 | 0 / 0 |

| Query order | Solver | Native median (ms) | Ordinary error | Distance error | DLmr error | Bcm error |
|---|---|---:|---:|---:|---:|---:|
| tiles32 | Compact | 37.67 / 37.99 | -5.0% / -6.4% | +10.6% / +9.6% | -5.0% / -6.4% | +7.8% / +6.7% |
| tiles32 | Binary lifting | 37.65 / 37.64 | -5.8% / -6.0% | -1.3% / -1.8% | -5.8% / -6.0% | +2.6% / +1.9% |
| tiles4096 | Compact | 45.84 / 45.23 | -21.9% / -21.4% | +22.8% / +25.7% | -21.6% / -21.1% | +16.7% / +19.2% |
| tiles4096 | Binary lifting | 41.23 / 40.87 | -14.0% / -13.5% | +0.3% / +0.2% | -11.7% / -11.2% | +5.5% / +5.1% |
| reverse_tiles | Compact | 36.25 / 36.31 | -1.3% / -2.1% | -1.1% / -1.9% | -1.3% / -2.1% | -1.0% / -1.9% |
| reverse_tiles | Binary lifting | 35.75 / 35.39 | -0.8% / -0.1% | -0.8% / -0.0% | -0.8% / -0.1% | -0.7% / +0.0% |

The largest DLmr error came from the compact solver with 4,096-query groups. Both guests produced 81,782 simulated read misses for ascending queries, 2,162,617 for the full shuffle and 91,608 for the held-out stream. The held-out count covers only 0.47% of the endpoint span. It predicted 35.94 / 35.71 ms for a native 45.84 / 45.23 ms. At 40 ms, both implementations received a predicted pass while all nine native observations of each exceeded the limit on each guest. At 45 ms, the compact solver again received a predicted pass, with seven of nine native runs over budget on the first guest and six of nine on the second. The simulated branch count covered 54.67% of its endpoint span and overshot the native time. This points to a limitation of translating a single event count into elapsed CPU time.

For the reverse-group stream, DLmr fell below the ascending endpoint on both executables and guests. The registered clamp used the ascending native median in all four instances. All other DLmr observations and all distance and Bcm observations lay within their respective endpoint ranges. No endpoint features were equal.

Query distance had the lowest overall median error, driven by binary lifting (per-solver median 0.77% / 0.18%). For the compact solver its median was 10.62% / 9.63%, and its worst error exceeded 22% on both guests. Bcm placed all six pairs within 20% on both guests and matched 58 of 60 majority verdicts. Its two errors on each guest were false timeouts against unanimous native passes. The same Cachegrind execution collected DLmr and Bcm, so the measured cost includes both simulations. The study did not time branch-only profiling.

## Cost and repeatability

Across ten input/executable pairs, Cachegrind consumed 11.33 / 11.27 seconds of child CPU. Ordinary Durante consumed 1.77 / 1.80 seconds. The median of the ten paired cost ratios was 28.91× / 29.01× versus direct native execution and 6.36× / 6.29× versus ordinary Durante. All figures include process startup. The midpoint needs only endpoint native timings, query distance also needs the judge input, and the simulator methods additionally require instrumentation on the endpoints and on each new input.

Both fresh Debian 12 N2D guests used AMD Milan, one hardware thread per core and CPU 1 affinity plus a CPU 1 cgroup for all 110 measured processes per guest. The kernel traces show zero event loss and the CPU steal counter delta was zero on both. All 13 simulated event counts reproduced exactly for each of the ten pairs. The absolute change in native medians relative to the first guest was 0.86% at the median and 1.44% at the maximum. An initial setup attempt stopped on the first Cachegrind invocation because the target retained the build host's ownership when extracted. It produced no native timings. Its VM and disk were deleted, and the successful runs used the original frozen archive extracted under the guest runner's ownership. [First guest receipt](../experiments/results/gce-scrivener-cache/collection.json) · [Second guest receipt](../experiments/results/gce-scrivener-cache-repeat/collection.json)

These forecasts use two paid native reference inputs per executable for the interpolation methods. The three held-out orders come from a single problem, with the same query multiset, and cover two known algorithms on one CPU family. The next test of the branch signal should use an independent problem and a separately timed lower-cost collector before considering it for a judge clock.

## Replay

Run the following from the repository root to check both archives against their published metrics.

```sh
tmp=$(mktemp -d)
for suffix in '' '-repeat'; do
    result="experiments/results/gce-scrivener-cache${suffix}"
    work="$tmp/boot${suffix}"
    mkdir "$work"
    tar xzf "$result/output.tar.gz" -C "$work"
    python3 -B experiments/scrivener_cache/verify.py "$work/output" --output "$work/metrics.json"
    cmp "$result/metrics.json" "$work/metrics.json"
done
python3 -B -m unittest discover -s tests -p 'test_scrivener_cache*.py' -v
```

To build a guest, supply the registered `bundle.tar.gz` from the Durante #97 artifact and the amd64 Debian 12 Valgrind package. On a suitable Debian 12 build host the latter is available with `apt download valgrind:amd64=1:3.19.0-1`. The builder verifies both inputs, rebuilds both static workloads and checks their ELF digests. When transferring the directory in a tarball, extract it as the guest runner with `tar --no-same-owner` so locally built mode-0700 executables belong to that account. On the disposable Debian 12 guest, run `sudo taskset -c 0 ./guest.sh` from the resulting payload directory. [Guest builder](../experiments/scrivener_cache/prepare.py) · [Durante #97 artifact](https://github.com/seo-rii/durante/actions/runs/45640347158/artifacts/15033563582)

```sh
python3 -B experiments/scrivener_cache/prepare.py "$tmp/guest" \
    --current-bundle "$bundle97" --valgrind-deb "$valgrind_deb"
```

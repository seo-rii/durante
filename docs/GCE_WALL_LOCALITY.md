# Can input locality correct Wall deadline errors?

Across five previously untimed range-update orders, adding input locality lowered the **worst absolute timing error** from **10.83% / 11.83%** to **6.44% / 5.34%** on the two pinned guests. It also reduced unanimous false passes from **10 / 12 to 2 / 2**. It introduced **three unanimous false timeouts per guest**, all on the same page-round-robin input. The estimator met the preregistered worst-error criterion and failed the separate deadline criterion. [First guest metrics](../experiments/results/gce-wall-locality/metrics.json) · [Second guest metrics](../experiments/results/gce-wall-locality-repeat/metrics.json)

## Prospective experiment

The earlier Wall study found a specific false pass. In bit-reversed order, the same executable ran for 55.76 / 56.57 ms on two CPU-pinned guests while its branch-only estimate was under 48 ms on both. A standalone input scan can distinguish updates that repeatedly revisit a small set of logical pages from updates that cycle through many. We use that result to design one new estimator, then evaluate it on previously untimed streams. [Earlier bit-reversed results](GCE_WALL_BRANCH.md) · [Registration, input hashes and acceptance criteria](../experiments/wall_locality/plan.json)

**Retrospective control.** On the old singleton inputs, the new scan counts 256 misses for ascending order, 122,949 for a full shuffle and 131,072 for bit reversal. The last value exceeds the shuffled endpoint. The combined forecast therefore takes the shuffled native reference and predicts 56.443 / 56.979 ms. This corrects both 50 ms false passes on the example used to design it. The prospective scores below use a different corpus. [Replay against the two earlier raw archives](../tests/test_wall_locality_history.py)

**New inputs.** Each input applies 131,065 overlapping raises of width eight to 131,072 cells of IOI 2014 Wall. The starting cell determines the height. All except the seven cells at each end receive eight raises, so an independent oracle can compute the final height directly as the maximum of its covering updates. All seven orders have identical update multisets, 2,916,091 update-node visits excluding final output, and identical output. The native references use ascending and globally shuffled starting positions. The five held-out streams shuffle within 128- or 8,192-cell tiles, traverse residue classes modulo 64 or 512, or shuffle 256-cell tiles while preserving order inside each tile. The modulo-512 stream visits one offset on each logical page before proceeding to the next offset. [Official IOI task](https://ioinformatics.org/files/ioi2014problem2.pdf) · [Input generator](../experiments/wall_locality/corpus.py) · [Wall implementation](../experiments/contest_accuracy/wall.cpp)

**Locality feature.** For each update, the standalone executable reads the left endpoint followed by the right. The logical page number is the integer cell index divided by 512. A fully associative list of the 16 most recently used pages starts empty. Each endpoint access either refreshes its page or increments the miss count and inserts it. The program visits only two endpoints per update irrespective of interval length. This deliberately coarse representation groups 512 eight-byte clamp slots per logical 4 KiB page. It omits internal tree nodes, actual address alignment, cache hierarchy and hardware prefetch. It reads only the problem input, so an immutable input's count can be reused across observations. The validation here covers the registered Wall solver and this workload. [Native input scan](../experiments/wall_locality/leaf_pages.cpp) · [Independent Python count](../experiments/wall_locality/pages.py)

**Forecast rule.** Normalize each event separately using its ascending and shuffled counts, clamping its fraction to the interval from zero to one (one half if the endpoint counts match). The candidate takes the larger of the fraction from simulated conditional branch misses and the fraction from logical-page misses. It interpolates between the two native time medians with that fraction and rounds up once. The five baselines or controls are ordinary Durante, the midpoint, branch misses alone, simulated last-level read misses alone and logical-page misses alone. A seventh forecast uses the larger fraction from conditional branch misses and last-level data read misses recorded by full Cachegrind. A pass includes equality at the deadline. [Forecasts](../experiments/wall_locality/score.py) · [Cachegrind event definitions](https://valgrind.org/docs/manual/cg-manual.html)

**Collection and qualification.** Each fresh AMD Milan guest uses the same frozen payload. All measured children run on CPU 1 with one hardware thread per core. Four rotated rounds cover every profiling arm and input. Only the first report from each arm supplies forecast features. After 112 profile runs and 18 native runs on the two anchors, the collector writes all 35 forecasts and a hash of the preceding 130 process records. It then runs the 45 held-out native trials. The native reference is the median child user-plus-system CPU time of nine runs per input. The primary test requires lower worst-case absolute relative error than branch-only on both guests. The separate deadline test requires fewer unanimous false passes and no new unanimous false timeouts on each guest over the fixed integer budgets from 30 through 120 ms. [Collector](../experiments/wall_locality/bench.py) · [Raw replay](../experiments/wall_locality/verify.py)

## Timing accuracy and deadline errors

Pairs are first guest / repeat guest. Each guest has 455 comparisons, representing 91 nested deadlines applied to the same five held-out inputs. A unanimous mismatch means all nine native observations disagree with the prediction.

| Forecast | Median absolute error | Worst error | Majority matches (of 455) | Unanimous false passes | Unanimous false timeouts |
|---|---:|---:|---:|---:|---:|
| Ordinary Durante | 7.07% / 7.63% | 10.83% / 11.83% | 435 / 434 | 18 / 20 | 0 / 0 |
| Two-anchor midpoint | 2.20% / 3.43% | 7.95% / 7.62% | 444 / 445 | 2 / 3 | 5 / 5 |
| Branch-only Bcm | 1.52% / 1.68% | 10.83% / 11.83% | 442 / 441 | 10 / 12 | 0 / 0 |
| Full DLmr | 3.26% / 3.06% | 10.94% / 10.36% | 440 / 442 | 8 / 8 | 2 / 4 |
| Logical pages | 4.73% / 4.97% | 10.19% / 10.29% | 441 / 442 | 10 / 9 | 3 / 3 |
| Branch plus input pages | 1.51% / 1.67% | 6.44% / 5.34% | 448 / 448 | 2 / 2 | 3 / 3 |
| Full Bcm plus DLmr | 1.48% / 1.60% | 10.94% / 9.97% | 447 / 449 | 0 / 0 | 2 / 4 |

| Order | Native CPU (ms) | Branch forecast (ms) | Branch + pages (ms) | Joint full-simulation forecast (ms) | Input page misses |
|---|---:|---:|---:|---:|---:|
| ascending | 49.95 / 49.96 | — | — | — | 256 |
| shuffled | 59.63 / 59.48 | — | — | — | 124,474 |
| tiles128 | 51.64 / 51.54 | 52.39 / 52.36 | 52.39 / 52.36 | 52.39 / 52.36 | 256 |
| tiles8192 | 55.66 / 55.74 | 55.73 / 55.65 | 55.73 / 55.65 | 55.73 / 55.65 | 730 |
| stride64 | 53.75 / 54.09 | 49.95 / 49.96 | 51.21 / 51.20 | 59.63 / 59.48 | 16,384 |
| page_round_robin | 56.02 / 56.67 | 49.95 / 49.96 | 59.63 / 59.48 | 57.00 / 56.90 | 131,072 |
| shuffled_tiles | 50.76 / 50.85 | 49.99 / 50.00 | 49.99 / 50.00 | 50.00 / 50.01 | 740 |

**At 50 ms**, all nine runs of each held-out input exceeded the limit on both guests. The branch clock predicted the correct verdict for 2/5 inputs on each. Adding page counts raised that to 4/5 and 5/5. For `shuffled_tiles`, its estimates were 49.991706 / 50.001090 ms. Small changes in the two native calibration medians put the forecasts on different sides of the same deadline despite identical event counts.

**For the page-round-robin update order**, the first-observation Bcm falls below the ascending reference. Branch interpolation therefore clamps to the ascending time. The input scan counts 131,072 logical page misses, above the 124,474 in the globally shuffled reference. The combined model clamps to the other endpoint and predicts 59.631 / 59.483 ms versus a native median of 56.023 / 56.669 ms. It removes the false pass at 50 ms and creates false timeouts at 57, 58 and 59 ms. All nine native runs passed each of those three limits on both guests. The logical working-set measure captures a locality change but saturates before the native timing does.

For `stride64`, branch misses also fall below the ascending endpoint, while the input-page miss count (16,384) is between endpoints. Combining them raises the forecast partway. Full DLmr exceeds its shuffled endpoint on this stream, so the joint full-simulation forecast takes the shuffled time. The two locality features have different failure cases. On the two within-tile shuffles the branch fraction dominates and the input scan leaves the estimate unchanged.

## Cost and repeatability

Each value here is median child user-plus-system CPU time for four fresh processes, including startup. The combined cost is the sum of the branch-only and input-scan medians for the same input. Overall ratios are the medians of the seven input-specific ratios.

| Order | Input scan CPU (ms) | Branch-only CPU (ms) | Full simulation CPU (ms) |
|---|---:|---:|---:|
| ascending | 7.42 / 7.47 | 1213.67 / 1217.68 | 2292.14 / 2296.61 |
| shuffled | 8.74 / 8.72 | 1246.53 / 1253.38 | 2378.83 / 2387.35 |
| tiles128 | 7.46 / 7.51 | 1218.86 / 1219.55 | 2291.07 / 2289.56 |
| tiles8192 | 9.27 / 9.35 | 1223.45 / 1221.89 | 2327.10 / 2337.72 |
| stride64 | 7.61 / 7.68 | 1219.21 / 1230.03 | 2325.74 / 2328.22 |
| page_round_robin | 7.99 / 8.09 | 1220.27 / 1216.34 | 2333.15 / 2341.08 |
| shuffled_tiles | 7.42 / 7.49 | 1212.59 / 1212.83 | 2286.80 / 2283.80 |

The scan added 7.42 / 7.47 ms at the low end and 9.27 / 9.35 ms at the high end on the two guests. The combined cost was 52.97% / 53.16% of full Cachegrind and 3.63× / 3.56× ordinary Durante. The input-only count can be cached per input. Branch-only profiling still requires running the executable. Calibration also requires two same-executable reference inputs with nine direct runs each in this protocol.

Both runs used the same frozen payload on different fresh Debian 12 AMD Milan guests. All 504 simulated event counts and 56 input-probe counts reproduced between boots (seven inputs, four rounds per mode). The native medians changed by 0.19% at the median and 1.15% at most relative to the first guest. The kernel traces place all 350 measured processes on CPU 1. The trace statistics showed zero losses and CPU 1's steal counter stayed constant on both guests. Both VMs, boot disks and temporary SSH keys were removed. [First receipt](../experiments/results/gce-wall-locality/collection.json) · [Second receipt](../experiments/results/gce-wall-locality-repeat/collection.json)

The results cover one Wall executable, positive raises on equal-width ranges, and one CPU family. They support using the probe to diagnose locality errors. The new unanimous false timeouts rule out this formula as a default clock under the registered deadline criterion.

## Replay

From the repository root, extract each archive and compare the rebuilt report with the published file.

```sh
tmp=$(mktemp -d)
for suffix in '' '-repeat'; do
    result="experiments/results/gce-wall-locality${suffix}"
    work="$tmp/boot${suffix}"
    mkdir "$work"
    tar xzf "$result/output.tar.gz" -C "$work"
    python3 -B experiments/wall_locality/verify.py "$work/output" --output "$work/metrics.json"
    cmp "$result/metrics.json" "$work/metrics.json"
done
python3 -B -m unittest discover -s tests -p 'test_wall_locality*.py' -v
```

The guest builder consumes the registered Durante #97 archive and Debian amd64 Valgrind 3.19.0-1 package, verifies the workload and helper source hashes, rebuilds the static ELFs and checks their hashes. Extract a transferred payload as the guest runner with `sudo tar --no-same-owner`, then run `sudo taskset -c 0 ./guest.sh` in its directory on a disposable Debian 12 guest. The guest wrapper pins the measured children to CPU 1. [Builder](../experiments/wall_locality/prepare.py) · [Release artifact](https://github.com/seo-rii/durante/actions/runs/45640347158/artifacts/15033563582)

```sh
python3 -B experiments/wall_locality/prepare.py "$tmp/guest" \
    --current-bundle "$bundle97" --valgrind-deb "$valgrind_deb"
```

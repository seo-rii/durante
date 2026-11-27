# Branch-only profiling and IOI Wall timing

Wall-specific calibration of simulated conditional branch misses met both registered comparisons. Across three unseen update orders, median absolute error was **0.54% / 0.48%** on the two guests, compared with **9.23% / 9.43%** for ordinary Durante and **7.50% / 8.43%** for the midpoint of two native reference times. Running only the branch simulator cost **52.6% of full simulation** on each guest. The bit-reversed stream still received a false pass at 50 ms on both. [First guest metrics](../experiments/results/gce-wall-branch/metrics.json) · [Second guest metrics](../experiments/results/gce-wall-branch-repeat/metrics.json)

## Workload and forecasts

The previous Scrivener study collected simulated branch misses together with cache misses. Branch misses alone predicted all six held-out CPU times within 20% on two guests, but the combined profiler cost over six times ordinary Durante. This study measures the cost of collecting only branch events and tests the same forecasting rule on a different problem. [Scrivener study](GCE_SCRIVENER_CACHE.md) · [Prospective registration](../experiments/contest_branch/plan.json)

IOI 2014 Wall applies height constraints to intervals. The existing C++ submission uses a segment tree with lazy propagation. Each new input raises every position in a wall of 131,072 positions exactly once with a singleton update. The position determines the height, so the final output is independent of update order and can be checked without a tree. Each order issues the same updates, makes the same 17 descents per update, visits every node the same number of times and emits the same 786,432 output bytes. The two reference orders are ascending positions and a full shuffle. Two held-out streams shuffle positions within consecutive groups of 32 and 4,096. The third visits positions in bit-reversed order, obtained by reversing the 17 bits of the sequential index. [Official task](https://ioinformatics.org/files/ioi2014problem2.pdf) · [Solver](../experiments/contest_accuracy/wall.cpp) · [Corpus](../experiments/contest_branch/corpus.py)

Each fresh AMD Milan guest runs three rotated profiling rounds on CPU 1. The ordinary arm uses the released Durante #97 instruction/REP client. The other arms run Debian Cachegrind 3.19.0 with branch prediction enabled, once with cache simulation enabled and once with it disabled. The full mode uses 32 KiB L1 instruction and data caches and a 512 KiB last-level cache, all 8-way with 64-byte lines. The branch-only mode emits five events. Bcm is the simulated conditional branch misprediction count and DLmr is the simulated last-level data read miss count. They describe the simulated whole program, including its parsing and printing. [Cachegrind manual](https://valgrind.org/docs/manual/cg-manual.html) · [Collector](../experiments/contest_branch/bench.py)

The collector uses the **first** profile in each mode for forecasting. Repeats measure collection cost and variability. After the 45 profiles and 18 direct runs of the two reference orders, it writes the predictions and the digest of all 63 preceding process records. Only then does it launch the 27 native runs of the three held-out orders. Each reference or held-out time is the median of nine child user-plus-system CPU observations.

The ordinary clock scales the ascending native time by the ratio of Durante work counts. The midpoint uses the mean of both Wall reference times. The two interpolations place Bcm (branch-only) or DLmr (full) between their respective endpoint counts, clamp to the measured span and interpolate Wall CPU time. Equal endpoint counts select the midpoint. A separate transfer estimate takes the ascending Wall native time and adds **146 ns** for each additional simulated conditional miss. This is the rounded median of four Scrivener slopes from two executables on two boots. This last estimate uses one Wall reference time and measures how well a previously fitted per-miss cost transfers. [Forecast arithmetic](../experiments/contest_branch/score.py) · [Independent slope calculation](../tests/test_contest_branch_score.py)

## Held-out CPU time and verdicts

Results are first guest / second guest. Each guest contributes three held-out inputs and 81 fixed-limit comparisons, at 20–150 ms in 5 ms increments. A run at the deadline passes. The native reference was unanimous at 80 of 81 limits on the first guest and all 81 on the second. The last two columns count disagreements with unanimous native runs in the indicated direction.

| Forecast | Median absolute error | Worst error | Within 20% (of 3) | Majority matches (of 81) | Unanimous false TLE | Unanimous false pass |
|---|---:|---:|---:|---:|---:|---:|
| Ordinary Durante | 9.23% / 9.43% | 16.24% / 17.59% | 3 / 3 | 78 / 78 | 0 / 0 | 2 / 3 |
| Endpoint midpoint | 7.50% / 8.43% | 9.24% / 9.83% | 3 / 3 | 79 / 79 | 1 / 1 | 0 / 1 |
| Branch-only Bcm | 0.54% / 0.48% | 13.94% / 15.18% | 3 / 3 | 79 / 79 | 0 / 0 | 1 / 2 |
| Full simulation DLmr | 1.23% / 1.16% | 9.23% / 9.43% | 3 / 3 | 80 / 80 | 0 / 0 | 1 / 1 |
| Scrivener 146 ns/Bcm | 9.31% / 9.24% | 49.52% / 49.30% | 2 / 2 | 75 / 75 | 6 / 6 | 0 / 0 |

| Update order | Native CPU (ms) | First Bcm, branch-only | Ordinary forecast (ms) | Bcm forecast (ms) | Transferred forecast (ms) |
|---|---:|---:|---:|---:|---:|
| ascending | 46.70 / 46.62 | 3,220,529 | — | — | — |
| shuffled | 56.44 / 56.98 | 3,671,733 | — | — | — |
| tiles32 | 47.21 / 47.16 | 3,254,121 | 46.70 / 46.62 | 47.43 / 47.39 | 51.61 / 51.52 |
| tiles4096 | 51.45 / 51.47 | 3,427,590 | 46.70 / 46.62 | 51.17 / 51.37 | 76.93 / 76.85 |
| bit_reversed | 55.76 / 56.56 | 3,279,840 | 46.70 / 46.61 | 47.98 / 47.98 | 55.36 / 55.27 |

The two tile widths account for the small median error. For bit-reversed positions, Bcm traverses only 13.15% of the ascending-to-shuffled span while native time traverses 92.95 / 96.01% of its span. The branch predictor underestimates native CPU time by 13.94 / 15.18%. At 50 ms all nine native trials exceeded the limit on both guests. At 55 ms, eight of nine exceeded it on the first guest and all nine on the second. The model predicted a pass at both limits on both guests.

For the full simulator, DLmr falls just below the ascending reference on both tile widths and exceeds the shuffled reference on the bit-reversed input. All three DLmr estimates therefore clip to a reference time. Bcm stays in range on all three streams. This difference helps explain why the full simulator's DLmr forecast is closer on the bit-reversed input. The study collected no hardware events to attribute the extra time to a specific processor component.

Using Wall reference times gives an empirical endpoint slope of 21.59 / 22.97 ns per simulated miss on the two boots. Transferring the Scrivener value of 146 ns per miss instead overestimates the 4,096-cell tile time by 49.52 / 49.30%. The feature and interpolation rule transferred across these two problems when given two Wall reference times. The numeric Scrivener coefficient performed poorly on this Wall input. None of these fitted slopes measures the physical penalty of a branch misprediction.

## Profiling cost and replication

Each cell is median child CPU time from three rotated executions, including startup. The reported overall ratios are the medians of the five within-input ratios.

| Update order | Ordinary CPU (ms) | Branch-only CPU (ms) | Full simulation CPU (ms) |
|---|---:|---:|---:|
| ascending | 326.10 / 325.04 | 1161.78 / 1158.74 | 2196.53 / 2189.86 |
| shuffled | 347.46 / 345.88 | 1199.03 / 1195.17 | 2290.57 / 2282.05 |
| tiles32 | 328.24 / 341.31 | 1161.31 / 1159.75 | 2188.08 / 2190.61 |
| tiles4096 | 344.24 / 330.35 | 1167.97 / 1168.95 | 2220.55 / 2221.56 |
| bit_reversed | 347.92 / 346.71 | 1200.04 / 1198.16 | 2291.36 / 2298.78 |

Turning off cache simulation saved about 47.4% of full profiling CPU on each guest. The branch-only collector still used 3.45× / 3.46× ordinary Durante CPU and 22.70× / 22.71× direct native CPU. Its five shared event counts matched the full simulator for all fifteen case/round pairs per guest. Across boots all 270 simulated count values were identical. Native medians differed by 0.19% at the median and 1.45% at the maximum relative to the first guest. The kernel traces verify CPU 1 placement of all 180 measured processes with zero trace loss and zero CPU steal ticks. Both temporary VMs, boot disks and SSH keys were removed. [First guest receipt](../experiments/results/gce-wall-branch/collection.json) · [Second guest receipt](../experiments/results/gce-wall-branch-repeat/collection.json)

This comparison covers one trusted executable and one matched point-update workload on a single CPU family. A candidate judge clock would still have to handle previously unseen implementations, range updates and boundary verdicts without calibrating two reference inputs per new executable (nine native runs per reference input here). The bit-reversed stream supplies a regression case for any cheaper combined branch and locality estimate.

## Reproduce

The archives contain the raw wait4 observations, ordinary process records, Cachegrind event files and logs, kernel exec trace and the pre-label prediction seal. From the repository root, the following commands reproduce both metrics files byte-for-byte.

```sh
tmp=$(mktemp -d)
for suffix in '' '-repeat'; do
    result="experiments/results/gce-wall-branch${suffix}"
    work="$tmp/boot${suffix}"
    mkdir "$work"
    tar xzf "$result/output.tar.gz" -C "$work"
    python3 -B experiments/contest_branch/verify.py "$work/output" --output "$work/metrics.json"
    cmp "$result/metrics.json" "$work/metrics.json"
done
python3 -B -m unittest discover -s tests -p 'test_contest_branch*.py' -v
```

To build the guest, supply the registered `bundle.tar.gz` from #97 and the amd64 Debian 12 package `valgrind=1:3.19.0-1`. The builder verifies both packages, the Wall source, and the resulting static ELF and tool hashes. On a disposable Debian 12 guest, extract a transferred payload with `sudo tar --no-same-owner`, then run `sudo taskset -c 0 ./guest.sh` from the payload directory. The wrapper places measured children on CPU 1. [Builder](../experiments/contest_branch/prepare.py) · [Durante #97 artifact](https://github.com/seo-rii/durante/actions/runs/45640347158/artifacts/15033563582)

```sh
python3 -B experiments/contest_branch/prepare.py "$tmp/guest" \
    --current-bundle "$bundle97" --valgrind-deb "$valgrind_deb"
```

# Measuring the distance and age of Scrivener page revisits

The displacement of a same-page revisit improved both registered accuracy and deadline criteria on **both** CPU-pinned Milan guests. Across 16 fresh input/solver pairs, worst absolute error fell from **33.77% to 12.65%** on the first boot and from **29.10% to 7.57%** on the second. Of 192 deadlines per guest, it fixed four and six unanimous false timeouts respectively, with no newly wrong unanimous decision. One unanimous false timeout remained on the first boot, for the compact solver at 55 ms on the 160-query, 36-lane input. The result covers these two known solver binaries and two paid calibration inputs per solver. [Primary results](../experiments/results/gce-scrivener-rank/metrics.json) · [Independent repeat](../experiments/results/gce-scrivener-rank-repeat/metrics.json)

## Predictions against native CPU time

Each cell gives the first and second boot results. Absolute timing error compares a forecast with the median of nine native runs. A unanimous error means all nine native runs agreed on the other verdict. Thirteen of 192 decisions on the first boot and twelve on the second had mixed native votes.

| Estimate | Median absolute error | Worst absolute error | Within ±20%, of 16 | Majority agreement, of 192 | Unanimous false timeouts | Unanimous false passes |
|---|---:|---:|---:|---:|---:|---:|
| Native anchor midpoint | 7.06% / 7.45% | 44.55% / 44.73% | 14 / 14 | 171 / 174 | 6 / 5 | 7 / 8 |
| Original branch/stream | 4.82% / 3.58% | 33.77% / 29.10% | 15 / 15 | 179 / 180 | 5 / 6 | 0 / 0 |
| Prior midpoint adjustment | 6.14% / 5.44% | 21.44% / 21.77% | 14 / 15 | 180 / 183 | 5 / 3 | 1 / 1 |
| Branch + revisit age | 4.02% / 3.84% | 16.49% / 14.72% | 16 / 16 | 183 / 186 | 1 / 1 | 1 / 2 |
| Branch + revisit displacement | 4.19% / 3.13% | 12.65% / 7.57% | 16 / 16 | 182 / 187 | 1 / 0 | 0 / 0 |

The preregistered primary candidate, **revisit age**, introduced one and two unanimous false passes. On the second boot it also raised median timing error. The **midpoint adjustment** from the retrospective comparison reduced worst error but raised median error and introduced a unanimous false pass on each guest. A nearly sorted input exposes its bias toward the middle. For adjacent query swaps in the compact solver, the adjusted values were 44.68 and 44.11 ms against native medians of 36.79 and 36.22 ms. [Earlier midpoint comparison](GCE_SCRIVENER_LAZY.md)

Two fresh inputs illustrate why the age threshold matters. When 192-query tiles were interleaved across 112 and then 144 lanes, the compact solver's native medians moved from 55.73 to 55.11 ms on the first boot and from 54.64 to 55.10 ms on the second. The number of qualifying aged misses jumped from 17,410 to 158,698. The age-based estimate rose by more than 13 ms on each guest. The displacement estimate changed by less than 0.5 ms on each. Native variation deserves separate attention. For compact 112 lanes on the first boot the nine native observations ranged from 52.61 to 62.31 ms despite CPU pinning. Zero steal ticks were recorded on both guests. [First journal](../experiments/results/gce-scrivener-rank/output.tar.gz) · [Second journal](../experiments/results/gce-scrivener-rank-repeat/output.tar.gz)

The extended scanner cost a median 7.86 and 7.89 ms of child CPU per input. A branch profile cost 529.17 and 537.10 ms per input/solver. Both candidate features were collected in the same scanner invocation. This experiment measures that combined cost. A paired comparison with the earlier four-field scanner would be needed to isolate the incremental cost.

## Historical deadline regressions

All times in this table are milliseconds. Each row is a previously measured input, separate from the fresh-input table.

| Compact solver regression and limit | Native median | Original clock | Revisit age | Revisit displacement | Native timeout votes, of 9 |
|---|---:|---:|---:|---:|---:|
| `bitrev_tiles256`, 45 ms | 41.43 / 40.32 | 42.34 / 41.82 | 42.34 / 41.82 | 42.34 / 41.82 | 0 / 0 |
| `lanes80`, 50 ms | 47.27 / 47.13 | 48.98 / 48.32 | 48.98 / 48.32 | 48.98 / 48.32 | 0 / 0 |
| `descending40`, 50 ms | 46.51 / 46.28 | 50.49 / 49.80 | 50.49 / 49.79 | 50.49 / 49.80 | 0 / 0 |
| `tiles96_lanes40`, 60 ms | 51.34 / 50.66 | 65.62 / 64.59 | 50.67 / 49.97 | 53.63 / 52.86 | 0 / 0 |

Both candidates repaired the original 60 ms false timeout on both boots. On the first boot the compact descending input still falsely timed out at 50 ms. Keeping the normalized branch fraction at half weight, even a hypothetical scan fraction of zero gives a lower bound of 50.485 ms. The slowest of nine native runs was 47.878 ms. Changing just the page statistic cannot recover this decision under that time mapping. The replay reports the whole interval of estimates attainable with any scan fraction in [0, 1] and the observed branch count for every control and fresh pair. This bound is a post-run diagnostic. [Replay](../experiments/scrivener_rank/verify.py)

## Why revisit size and age matter

On a previous 60 ms regression case the compact Scrivener solver finished in about 51 ms. Its page and branch features each predicted more than 64 ms. Reweighting those two normalized values could not fix the false timeout. The page feature gave a count of one to both a nine-position jump and a jump across most of a page. It also treated visits separated by a few dozen queries in the same way as much older visits. [Original counterexample](GCE_SCRIVENER_LAZY.md)

Both new features operate on the existing scanner's logical pages of 512 character positions. A *disruptive miss* occurs when a queried page is absent from the 16-entry LRU and the query either first visits that page or lands more than eight positions from the last query to it. The scanner updates the last position and query index on every lookup, including LRU hits.

- **Revisit age** counts a disruptive miss if it first visits the page or if at least 128 other lookups occurred since the preceding visit. The threshold is measured in lookup operations. A sequence with 128 intervening queries qualifies and one with 127 does not.
- **Revisit displacement** sums the absolute distance, in character positions, between the current and previous queries to the same page, on disruptive misses. A first visit contributes zero distance.

Each candidate normalizes its extra feature between the ascending and globally shuffled inputs and clamps it to the unit interval. It takes the smaller of this fraction and the original disruptive-miss fraction, then averages with the normalized simulated branch mispredictions. This fraction interpolates between the same two native anchor medians for the same solver and boot, with one final round up to nanoseconds. If the extra feature has identical counts at both anchors, the candidate uses the original disruptive fraction. The experiment also tests the previously proposed average of the full clock and the anchor midpoint. A lower estimate can avoid a false timeout but can introduce a false pass. [Exact formulas](../experiments/scrivener_rank/score.py) · [Scanner](../experiments/scrivener_streams/scan.cpp)

## Prospective test

The registration fixes the entire comparison before measuring fresh native labels. Eight new query orders exercise neighboring swaps in sorted order, a seven-eighths shuffled prefix followed by a sorted suffix, bit reversal in 128-query tiles, 56 descending interleaved lanes, and four shuffled-tile orders. Two of those use 192-query tiles and vary the interleaving from 112 to 144 lanes. The other two use 160-query tiles in 36 lanes and 768-query tiles in 52 lanes. All orders preserve the same document, multiset of 180,000 queried positions and expected characters. The compact jump-tree and binary-lifting solvers use the exact ELF binaries of the earlier experiments. Four inputs that informed the design are regression controls and are excluded from the fresh-input scores. [Frozen registration](../experiments/scrivener_rank/plan.json) · [Corpus](../experiments/scrivener_rank/corpus.py)

On each of two independent AMD Milan GCE guests, the harness restricts all measured processes to CPU 1 with one hardware thread per core. It collects three scans per input and two ordinary Durante profiles and two branch-only Cachegrind profiles per solver/input. It measures each anchor nine times per solver, then seals all predictions and a hash of the first 190 process receipts. Only then are the four controls and eight fresh orders timed natively nine times per solver. Runtime is child user plus system CPU. The branch data come from Cachegrind simulation and the page counts describe the query order in the input. [Guest collector](../experiments/scrivener_rank/bench.py)

Each candidate must reduce the worst absolute relative error and maintain or reduce the median absolute relative error against the original branch/stream clock on each boot. The separate deadline test requires at least one corrected unanimous native decision and zero new unanimous errors, comparing the same input, program and limit. The 16 fresh pairs yield 192 comparisons per boot over budgets of 30–85 ms in steps of 5 ms. Equality passes. Mixed native votes remain visible alongside the nine-run majority results.

## Reproduce the results

From the repository root, replay the original guest journals and compare all summary fields.

```sh
tmp=$(mktemp -d)
for suffix in '' '-repeat'; do
    result="experiments/results/gce-scrivener-rank${suffix}"
    dir="$tmp/boot${suffix}"
    mkdir "$dir"
    tar xzf "$result/output.tar.gz" -C "$dir"
    python3 -B experiments/scrivener_rank/verify.py "$dir/output" --output "$dir/metrics.json"
    cmp "$dir/metrics.json" "$result/metrics.json"
done
python3 -B -m unittest discover -s tests -p 'test_scrivener_rank*.py' -v
```

The registered builder compiles the revised scanner and verifies its ELF, each solver ELF, the production reader, the Durante runtime and the Cachegrind binaries. The four-field scanner of the earlier study still builds to its registered hash. [Payload builder](../experiments/scrivener_rank/prepare.py)

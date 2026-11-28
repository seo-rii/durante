# Loop recurrence on nine contest problem families

Charging one 3.1 GHz cycle per modeled cycle improves maximum-subarray timing and fixes one range-distinct decision. Across all 34 solver/reference comparisons on each Milan boot, median absolute relative error rises and the clustered predecessor solver gets a new false time-out. The candidate misses the registered requirement on both boots. This historical replay leaves the judging clock unchanged. [Registered comparison](https://github.com/seo-rii/durante/commit/e622190) · [Exact results](results.json)

| Measure | Primary baseline | Primary + recurrence | Repeat baseline | Repeat + recurrence |
| --- | ---: | ---: | ---: | ---: |
| Median absolute relative error | 12.69% | 13.17% | 13.74% | 15.58% |
| Worst absolute relative error | 291.03% | 288.15% | 298.86% | 295.92% |
| Within 20% of native ratio | 23/34 | 23/34 | 22/34 | 23/34 |
| Correct native-decisive deadlines | 148/161 | 150/161 | 146/155 | 147/155 |
| False accepts | 5 | 4 | 4 | 3 |
| False time-outs | 8 | 7 | 5 | 5 |
| Native-mixed deadlines | 9 | 9 | 15 | 15 |

For each executable the baseline uses the original Dijkstra ML4 nanosecond weights plus 6.92658480142496 ns per gshare miss. The candidate adds exactly `10/496 × loop_recurrence_cycles_x16` ns to that executable's score, then divides the submission score by the intended solver's score for the same input. This counter estimates register-dependency growth in a basic block that loops back to itself. The client compares four and eight simulated passes to obtain its per-execution recurrence cost. The predictor was already collecting this field. A modeled cycle corresponds to 16 counter units. Its price here is a fixed hypothesis and the recurrence term may overlap with the existing latency feature. The long maximum-subarray prefix/Kadane recurrence ratio had already been seen when choosing the hypothesis. Every family below uses archived data. [Feature implementation](../../time_features/featclient.c) · [Protocol](protocol.json)

| Family | Solver/input pairs per boot | Primary median error, baseline → candidate | Repeat median error, baseline → candidate |
| --- | ---: | ---: | ---: |
| Grid BFS | 2 | 14.09% → 14.13% | 15.80% → 15.85% |
| Dijkstra, original and layered | 6 | 17.50% → 17.17% | 25.13% → 24.43% |
| Fenwick | 2 | 10.97% → 16.15% | 13.01% → 19.39% |
| Ordered predecessor | 4 | 9.93% → 10.77% | 11.82% → 13.48% |
| Inversion counting | 4 | 4.34% → 8.86% | 3.86% → 8.75% |
| Range distinct | 4 | 5.85% → 3.89% | 5.67% → 3.83% |
| String matching | 4 | 86.30% → 86.16% | 81.58% → 81.44% |
| Longest common subsequence | 4 | 17.05% → 17.12% | 17.26% → 17.33% |
| Maximum subarray | 4 | 15.30% → 8.37% | 14.17% → 7.68% |

Every changed deadline can be traced to three groups. On both boots the clustered predecessor Fenwick/reference score rises from 0.9330× to 1.0018×, incorrectly timing it out at 1×. Runs-Mo on range distinct rises from 1.2428× to 1.2504×, correctly timing it out at 1.25×. For maximum subarray, the long prefix/reference ratio falls from 1.0000024× to 0.9668×, correcting the 1× false time-out on both boots. The short prefix also gets a correct 1× accept on the primary boot. Its repeat native range crosses that deadline. [Per-case scores, observed ranges and changed decisions](results.json)

For the original random-text Z/KMP problem the feature cannot correct the 1.5× false time-out on the first boot at *any* nonnegative price. The baseline ratio is 2.4114× and the recurrence count ratio is 2.4006×. Adding the same nonnegative price per count to both scores gives a weighted mean of these ratios, always above 1.5×. Every native sample pair on that boot was between 1.355× and 1.481×. This observation follows from the archived counters after the registered evaluation. [Matcher archive](../string_matching/)

## Reproduce

Run `OPENBLAS_NUM_THREADS=1 python3 -B -m unittest` and `OPENBLAS_NUM_THREADS=1 python3 -B replay.py --verify results.json` in this directory.

The replay covers all seven registered panels and 14 independent GCE boots, including 1,230 trials in the source archives. For six panels the 168 predictor records came from the same pinned guests as native timing. The original Milan guests predate the predictor. Their matching 18 local records cover two runs of each of nine registered ELF/input pairs. Both original GCE boots reuse those same local counts, so they supply one independent recurrence observation per executable after checking repeatability. Every archived solver in the named panels has a recurrence measurement. The original readers verify native times, meter validity, schedule, ELF/input/output identities, cache features and gshare misses. This replay also verifies the raw predictor archive identities, membership, integer counts and all repetitions. Each family summary gives equal weight to its solver/input pairs. The pooled median gives equal weight to all 34 pairs per boot. [Original Milan predictor evidence](../milan/gshare/) · [Baseline archive replay](../score_offset/)

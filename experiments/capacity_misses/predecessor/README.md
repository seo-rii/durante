# Removing the most expensive affordable item

For each budget, sell the single most expensive remaining item whose price is at most that budget. Report its price, or `-1` if no item fits. Duplicate prices represent distinct items. The suite compares an ordered multiset, a Fenwick frequency tree with rank selection, and a sorted array with disjoint sets that skip deleted positions. All three read the same input and print each individual answer. A Python oracle removes elements directly from a sorted list.

## Registered comparison

Two inputs contain 65,536 initial items and 73,728 purchases. Prices are between 1 and 131,072. Both inputs have identical initial prices and budgets as multisets. The uniform input preserves the seeded query order. The clustered input alternately sorts each successive block of 512 budgets in ascending and descending order. Reordering can change which sales succeed, so each input has its own full output oracle.

Each input uses its own disjoint-set solver as the reference. The instruction/REP model (M0) and the latency/cache model (ML4) use the fixed Dijkstra coefficients from the earlier Milan study. The clock for a submission is its model score divided by the corresponding reference model score. Native time is the submission CPU time divided by the reference CPU time. No predecessor observation participates in fitting the weights.

Each independent SMT-off Milan VM pins every measured execution to CPU 1. There are seven native and two full diagnostic runs per executable/input, or 54 runs per boot. A factor of 1.25, 1.5, 2 or 3 times the reference is an inclusive deadline. A comparison is determinate when every pair of native measurements gives the same decision and all diagnostic score pairs give the same decision. Report false accepts, false timeouts and indeterminate comparisons on each boot and when pooling both. Also report each median native ratio, signed relative timing error for both models and whether all four full-probe readings per program/input agree. These are simulated deadlines using completed observations.

`prepare.py` takes the registered corrected Milan payload, builds the solvers, checks each output and writes a deterministic guest archive and file manifest. The VM uses the previously published Milan collector and full cache probe unchanged.

## Results on two pinned Milan boots

All six program/input pairs returned the full expected output and had identical counters in four diagnostic runs. Both guests recorded zero CPU steal ticks. Across the 16 registered comparisons, the full ML4 model matched every native verdict on each boot and across their pooled ranges. M0 matched 12. It falsely accepted the multiset at 1.25× and 1.5× on both inputs.

| Implementation and input | Native/reference median, boot A / B | M0 prediction | ML4 prediction |
| --- | ---: | ---: | ---: |
| Multiset, uniform | 1.626 / 1.597 | 1.195 | 1.803 |
| Multiset, clustered | 1.746 / 1.657 | 1.195 | 1.820 |
| Fenwick, uniform | 0.868 / 0.875 | 1.051 | 1.062 |
| Fenwick, clustered | 0.928 / 0.925 | 1.051 | 1.071 |

The uniform Fenwick time estimate was 21.4–22.4% high for ML4. At the reference's own 1× deadline, which was examined after collection, every Fenwick run was faster than every reference run on the same input, yet both models timed it out on both inputs. This exploratory denominator is four solver/input comparisons, separate from the sixteen registered comparisons. Full-probe CPU cost ranged from 71.77× to 114.37× native over the six cases and two boots.

The two [raw guest archives and deletion receipts](evidence) cover 84 native and 24 diagnostic executions. From this directory, replay the counters, CPU placement, outputs and all decisions with `python3 -B replay.py --verify results.json`. Run `python3 -B -m unittest -v test_predecessor` for the oracle and replay regressions.

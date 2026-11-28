# Recurrence-weighted clock on a weighted path

The frozen recurrence candidate met the registered relative-timing criterion on both held-out GCE boots. It reduced the median absolute error across four solver/input ratios from **11.54% to 2.52%** on the first boot and from **8.38% to 2.81%** on the second. Each boot recovered the same false timeout for the long table implementation at 1.5 times the reference limit. No previously correct deadline decision regressed.

| Alternative | Native / rolling reference, primary | Native / rolling reference, repeat | Base prediction | Recurrence prediction |
| --- | ---: | ---: | ---: | ---: |
| Short delta | 0.983 | 1.010 | 1.018 | 1.010 |
| Short full table | 1.545 | 1.591 | 1.846 | 1.510 |
| Long delta | 0.991 | 1.014 | 1.014 | 1.009 |
| Long full table | 1.239 | 1.275 | 1.535 | 1.366 |

Both boots had 17 determinate native decisions and three deadlines crossed by the range of native repetitions. The number correct rose from 15 to 16 on the first and from 16 to 17 on the second. The first boot still falsely timed out short delta at the 1× limit. All four candidate ratios were within 20% of the ratios of native medians on both boots.

## Workload and measurement

The task is to maximize the sum of vertex weights on a path without selecting adjacent vertices. The rolling reference keeps the best value for the previous two prefixes. The delta solver tracks the gain from one prefix to the next. The full-table solver stores every prefix answer. Each executable constructs the same signed-weight array from a compact seed input. The short path is the first 262,144 vertices of the 2,097,152-vertex long path. The Python oracle tracks the best answers that include or exclude the last vertex. Unit tests check the oracle against exhaustive independent subsets on small signed paths.

The absolute candidate score is ML4 plus 6.92658480142496 ns per gshare miss plus **10/496 ns per unit of** `loop_recurrence_cycles_x16`. The last term corresponds to one cycle at 3.1 GHz for every 16 units. The existing predictor collector emits that counter. The base score, weight, programs, input and output hashes, compiler, evidence rules and thresholds were fixed before measurement. [Model registration](https://github.com/seo-rii/durante/commit/e622190) · [Workload registration](https://github.com/seo-rii/durante/commit/c2dabd9e9560e9f5e77279c6322933a2d38e7a9e)

Each of the six cases ran seven times natively and twice in each of three diagnostic configurations on each of two new AMD Milan guests, for 156 executions. Each guest used CPU 1 with one thread per core and recorded zero steal ticks. The second boot reversed the schedule. Verdicts use every pairing of native CPU times and of model scores within a boot, with inclusive limits of 1, 1.25, 1.5, 2 and 3 times the rolling reference. Ratio errors use unrounded medians. The two predictor features used in an absolute score always come from the same execution.

The rolling and delta programs produced identical recurrence counts at each size. For the long input the additional term increased the rolling score from 11.731 to 19.215 ms and the full-table score from 18.008 to 26.253 ms, bringing their ratio closer to native. Generation, startup, allocation and the solver are all included. This experiment qualifies the reported comparison on this problem. Transfer to other families and accuracy in absolute CPU milliseconds require separate evidence.

Run `python3 -B -m unittest` and `python3 -B replay.py --verify results.json` from this directory. To rebuild the payload, run `python3 -B prepare.py --registered-tools PATH --output NEW_DIR` with the unpacked [range-distinct](../range_distinct/) tools. [Raw guest and teardown receipts](evidence/cloud.json) · [Full per-case decisions](results.json) · [Protocol](protocol.json)

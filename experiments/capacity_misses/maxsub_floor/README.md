# Transferring the reference score floor to maximum subarray

The baseline scores of the Kadane reference were 0.172 ms for the short input and 12.224 ms for the long input. These scores straddle the 7 ms floor as intended. The floor failed the preregistered criterion on both boots. It reduced false timeouts but introduced false accepts, and left the median absolute relative timing error unchanged. [Frozen score and floor](https://github.com/seo-rii/durante/blob/47f01b65aed66db0872e1aa4a02b2639ee7a968a/experiments/capacity_misses/score_offset/protocol.json) · [Holdout registration](https://github.com/seo-rii/durante/blob/a3605470692248ef20751ddf593cc268f1084a0f/experiments/capacity_misses/maxsub_floor/protocol.json)

The task is the maximum sum of a nonempty contiguous subarray. Each program constructs the same sequence of signed integers from the input count and xorshift32 seed. The first input contains 32,768 integers; the long input extends that exact prefix to 2,097,152. The reference uses Kadane's algorithm. An alternative tracks the minimum prior prefix sum. The recursive alternative scans both sides for the best crossing sum at each split. All timings include allocation and generation. A Python oracle composes segment totals, best prefixes, best suffixes and best subarray sums. Small exhaustive enumerations check its handling of all-negative inputs and single-element candidates in the combinations. [Programs](maxsub.cpp) · [Input and oracle](inputs.py) · [Registration manifest](registration.json)

The baseline score in ns is ML4 plus 6.92658480142496 ns per gshare miss. With submission and reference scores S and R, the global model adds 7,000,000 ns to both before division. The floor instead adds max(0, 7,000,000 − R) to both. Each reference belongs to the same input and boot as its submission. For the floor the replay calculates the correction separately for every score pairing. The native verdict uses all submission/reference CPU-time pairings and an inclusive deadline. Native-mixed cells are counted separately. [Exact endpoints and decisions](results.json)

| Alternative | Pooled median native | Baseline | Global offset | Reference floor |
| --- | ---: | ---: | ---: | ---: |
| short-prefix | 0.974377× | 1.000169× | 1.000004× | 1.000004× |
| short-divide | 1.622776× | 4.372261× | 1.080668× | 1.082645× |
| long-prefix | 0.949169× | 1.000002× | 1.000002× | 1.000002× |
| long-divide | 4.012263× | 4.933321× | 3.501128× | 4.933321× |

| Boot | Model | Median absolute ratio error | Correct | False accept | False timeout | Native mixed |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| First | Baseline | 15.30% | 15 | 0 | 4 | 1 |
| First | Global | 10.15% | 16 | 1 | 2 | 1 |
| First | Floor | 15.30% | 16 | 1 | 2 | 1 |
| Repeat | Baseline | 14.17% | 16 | 0 | 3 | 1 |
| Repeat | Global | 8.52% | 16 | 2 | 1 | 1 |
| Repeat | Floor | 14.17% | 16 | 2 | 1 | 1 |

The original model overestimates the short divide-and-conquer ratio at 4.372×. The floor brings it down to 1.083×, below the native pooled 1.623×. On the long input the floor leaves the point estimate unchanged. The models had identical global/floor classifications at the tested 1, 1.25, 1.5, 2 and 3 deadlines although their timing ratios differed. The four ratios come from two alternatives on two sizes of a single problem.

Each independent AMD Milan guest used one thread per core and CPU 1; the second boot reversed the schedule. All 156 runs passed the registered output check. Both guests recorded zero steal ticks. VM and boot disk deletion returned 404 for both runs, and both temporary keys were removed. [Receipts and raw archives](evidence/)

Run `python3 -B -m unittest` and `python3 -B replay.py --verify results.json` from this directory with the earlier studies' Python and NumPy environment. Rebuild the original payload with `python3 -B prepare.py --registered-tools PATH --output NEW_DIR` using the registered range-distinct tools. The LCS and maximum-subarray replays use the same validated guest reader; the unchanged LCS results also reproduce.

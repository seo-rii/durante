# How much of a relative time limit can an empirical interval decide?

A single simulated time ratio can give the wrong verdict even when its counters repeat exactly. This experiment uses the largest under- and overestimates observed on other programs to form an interval around the submission/reference score ratio. Each guest boot is scored separately. If the interval crosses the deadline, the method abstains.

The model, calibration families, graph workloads, five deadline factors, both calibration policies and the prospective success criterion were [published before collecting the layered graphs](https://github.com/seo-rii/durante/blob/0240a62/experiments/capacity_misses/relative_intervals/protocol.json). The [layered graph study](../dijkstra_relaxation/README.md) held out the inputs while reusing the same three Dijkstra executables. It tests transfer to different weights on related graphs. All runs used CPU-pinned AMD Milan VMs.

## What changes on the historical problems

Each target family was omitted in full, including both of its input patterns and both boots. The table gives outcomes for **one boot**; the results for these three families were identical on the second boot. Five deadlines on one solver/input comparison are related observations.

| Held-out problem | Point estimate correct | Interval correct | Interval abstained | Wrong issued by interval |
| --- | ---: | ---: | ---: | ---: |
| Predecessor | 18/20 | 16 | 4 | 0 |
| Inversions | 20/20 | 15 | 5 | 0 |
| Range-distinct queries | 19/20 | 12 | 8 | 0 |

The full three-family training set yielded multipliers of 0.929830 to 1.363980 (rounded). On the separate historical set-Dijkstra comparison, that interval abstained at 2× on both boots. The point estimate accepted on both. On the first boot every native observation exceeded 2×. Across both historical Dijkstra alternatives and five deadlines, the interval also issued two verdicts on native-mixed cells on the second boot. Removing the historical false accept alone does not qualify the method.

## New Dijkstra graphs

There are 20 cells per boot, with four solver/input comparisons at 1×, 1.25×, 1.5×, 2× and 3×. Every native observation in a cell must agree for that cell to have a determinate native verdict. The point estimate was correct on all 19 determinate cells of the first boot and all 20 on the second. The first boot's remaining cell straddled the native deadline.

| Calibration set | Boot | Issued on native-decisive | Wrong issued | Issued on native-mixed | Abstained across all 20 |
| --- | --- | ---: | ---: | ---: | ---: |
| Three families | First | 15/19 | 0 | 1 | 4 |
| Three families | Second | 16/20 | 0 | 0 | 4 |
| Three families + historical Dijkstra | First | 11/19 | 0 | 0 | 9 |
| Three families + historical Dijkstra | Second | 11/20 | 0 | 0 | 9 |

Adding the historical Dijkstra data raised the upper multiplier to 2.1686. It caught the earlier policy's confident 1× accept for the once-improved heap where the new native ratio ranged from 0.5873 to 1.0199. The wider policy met the registered criterion on both boots, at **55% coverage of all cells**. All its issued verdicts were accepts. It abstained on the many-improvements set at 1×, the one cell per boot where all native runs timed out. These observations do not validate issuing timeouts, and the empirical interval has no guaranteed coverage on a different problem, CPU or runtime.

## Reproduction and interpretation

For each case within a boot, form the native interval from the smallest solver CPU time over the largest reference time through the largest solver time over the smallest reference time. Form the same interval for the ML4 score plus 6.92658480142496 ns per simulated gshare miss. Each solver is compared with the intended implementation on that exact input. The corpus-wide lower multiplier is the smallest native lower bound divided by its model upper bound. The upper multiplier is the largest native upper bound divided by its model lower bound. Multiply the target model interval by the two corresponding bounds. Accept at an inclusive upper endpoint and time out strictly below the lower endpoint.

The replay reads native and counter receipts from the registered archives, reconstructs the original scores, and uses exact rational arithmetic for every comparison. For the historical contest Dijkstra, branch counts came from local runs on the registered executables and inputs, while cache and native readings came from GCE. The other studies collected all three kinds of measurements in the guests. The [case-level output](results.json) includes exact intervals, issued verdict counts by direction, and the outcome at every deadline in protocol order.

Run from this directory with the parent studies' Python and NumPy environment:

```sh
python3 -B -m unittest
python3 -B replay.py --prospective ../dijkstra_relaxation --verify results.json
```

Omit both arguments to replay only the historical problems. The registered prospective input is the sibling study from [the graph protocol commit](https://github.com/seo-rii/durante/blob/1c4c3018964f717c4fac8098613af29b32ce927a/experiments/capacity_misses/dijkstra_relaxation/protocol.json), plus its two published guest archives.

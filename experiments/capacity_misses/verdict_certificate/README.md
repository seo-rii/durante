# Three incompatible contest verdicts on pinned Milan CPUs

No nonnegative price on the 12 features below can reproduce all three observed time-limit decisions. This holds separately for the primary guests and for the repeat guests. It applies to an absolute score for each executable, with the same feature prices and intercept, divided by the reference score on the same input. The examples were selected after inspecting the archived outcomes. [Matcher archive](../string_matching/) · [Inversion-counting archive](../order_statistics_transfer/) · [Recurrence transfer study](https://github.com/seo-rii/durante/pull/139)

| Case | Reference | Deadline | Primary native ratio range | Repeat native ratio range | Verdict |
| --- | --- | ---: | ---: | ---: | --- |
| Naive matching, random text | KMP | 1× | 0.8349–0.9095 | 0.8304–0.9525 | AC |
| Naive matching, periodic text | KMP | 3× | 3.9439–4.4707 | 3.7145–4.2026 | TLE |
| Segment tree, shuffled inversion counting | Merge counting | 1× | 0.8910–0.9144 | 0.8710–0.9602 | AC |

Each range includes every pairing of a submission and reference CPU-time observation on the same guest. The verifier checks the original registrations, outputs, CPU 1 execution traces, cache and branch-client repetitions and archive hashes. The two studies used four distinct guest boots and boot disks. All features and native CPU times in each comparison came from that guest. [Exact bounds, observed feature vectors and integer rows](results.json)

## Integer certificate

Let `S` and `R` denote the submission and reference feature vectors. The coordinates are instructions, latency bound ×16, REP bytes, L1D misses, random misses at 2 MiB and 32 MiB, stream misses at 2 MiB, L1D-dependent misses, dependent misses at 2 MiB, gshare mispredictions, loop recurrence ×16, and a constant 1. A common nonnegative weight vector `w` prices these coordinates. The last weight is a common nonnegative absolute-score intercept. All reference coordinates are positive, so any nonzero admissible vector gives a positive denominator.

For the first row let `A = R − S`, for the second let `B = S − 3R`, and for the third let `C = R − S`. An accepted tie gives `A·w ≥ 0` or `C·w ≥ 0`. A timeout requires `B·w > 0`. Consequently `(180A + B + C)·w > 0`. On each boot set, the exact combined vector in the order above is

```text
-3474552  -15995544  -3968  -1880550  -35153  -226
  -93789       -154   -108   -209331 -4815216    -2
```

Every entry is negative. Its dot product with any nonnegative weights is at most zero. The all-zero vector also fails the strict timeout and gives an undefined score ratio. At least one of these native decisions must therefore be wrong for this feature set, even with a free choice of weights on each boot set.

The certificate limits this linear model. Other recorded events can change the sign of the combined vector. For example, the conditional-branch count gives +223,217,981 here. The separate historical set-Dijkstra ceiling likewise admits a 2.295× direct-call count ratio. Neither observation establishes an accurate price for a new feature. [Historical direct-call replay](../block_events/)

## Reproduce

Run `python3 -B -m unittest` and `python3 -B replay.py --verify results.json` in this directory. The proof uses integer arithmetic and rational native bounds. It requires no optimizer or new VM.

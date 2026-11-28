# Why the frozen ML4 score reverses Fenwick and DSU

Fenwick ran faster than DSU in every cross-pair of native observations on both predecessor inputs. The fixed Dijkstra ML4 score reverses that order. The additional latency-bound charge exceeds the credit for fewer dependent L1D misses. Values below are Fenwick minus DSU, pooled over both boots. The original weights are seconds per unit of each counter. Score milliseconds are before reference normalization. [Predecessor measurements](../README.md) · [Fixed coefficients](../frozen.json)

| Difference | Uniform | Clustered |
| --- | ---: | ---: |
| Native CPU medians, ms | −2.8175 | −1.4460 |
| Latency units (one sixteenth of a bound cycle) | +60,304,880 | +60,391,004 |
| Latency contribution, score ms | +1.182611 | +1.184300 |
| Dependent L1D misses | −113,300 | −93,458 |
| Dependent L1D contribution, score ms | −0.540443 | −0.445796 |
| Stream misses at 2 MiB, score ms | +0.043725 | +0.043693 |
| All L1D misses, score ms | +0.021050 | +0.020651 |
| Other four terms together, score ms | −0.000010 | +0.000098 |
| **Total Fenwick minus DSU score, ms** | **+0.706933** | **+0.802946** |

Tripling only the dependent-L1D weight puts both Fenwick scores below DSU, at 0.9701× and 0.9927×. It also causes false timeouts for both multisets at 2×. The replay uses the original observations for this retrospective sensitivity check.

## A score that preserves the tested verdicts

A score of `latency_x16 + 700 × l1d_dependent` meets all 31 determinate comparisons below and strictly ranks Fenwick ahead of DSU on both inputs. Each dependent L1D miss contributes 700 score units, each sixteenth-cycle contributes one, and the other six weights are zero. The comparisons use ratios, so a common positive scale cancels. This integer witness was chosen on the same data.

| Comparisons | Fixed ML4 | Integer witness |
| --- | ---: | ---: |
| Four registered predecessor limits on four variants | 16/16 | 16/16 |
| Predecessor at the reference's own time, retrospective | 2/4 | 4/4 |
| Same five reference multipliers on the two graph variants, retrospective | 8/9 | 9/9 |
| Original 65 ms graph deadline replayed on the newer timings | 2/2 | 2/2 |

The multipliers are 1, 1.25, 1.5, 2 and 3. The graph size-2,048 queue straddles 2× across native pairs, so that comparison is indeterminate. The 65 ms replay uses every original reference timing as a possible scale. Equality passes each deadline. The graph samples come from the separate flat-cache study of three shortest-paths executables. [Graph observations](../../flat_cache/README.md) · [Original graph deadline](../../aonohako_shortest_paths/README.md)

## Three stronger requirements conflict

The uniform Fenwick/DSU and multiset/DSU native ratios never exceed 19,355/21,682 and 36,853/21,682 respectively. The graph size-8 queue always exceeds 3× its own reference (at least 48,819/16,089). Even the following relaxed bounds cannot hold together for any nonzero nonnegative weighting of the eight counters.

Let `D, F, M` be the uniform DSU, Fenwick and multiset vectors, and `B, R` the graph size-8 queue and its reference. A score respecting those ratios would have nonnegative inner product with each of `0.9D − F`, `1.7D − M` and `B − 3R`. Multiply the first vector by ten, add the other two, then multiply by ten to obtain integers. Every coordinate is strictly negative:

```text
(-496647520, -45632, -75279744, -2775627, -45817, -2933082, -3243427, -483888)
```

The columns are latency bound ×16, REP bytes, L1D misses, random 2 MiB misses, random 32 MiB misses, stream 2 MiB misses, dependent L1D misses and dependent 2 MiB misses. The inner product of this sum with any eligible weight vector is strictly negative, contradicting the three requirements. This uses two native ratio bounds from predecessor in addition to a graph verdict. The 31 verdict checks impose looser demands on the two predecessor ratios.

From this directory, run `python3 -B replay.py` and `python3 -B -m unittest discover` using the parent studies' Python and NumPy environment. The replay reads four published guest archives, verifies counter agreement and computes the score contributions and inequalities with exact arithmetic.

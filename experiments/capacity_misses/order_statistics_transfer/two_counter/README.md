# A frozen two-counter score on inversion counting

Both scores agreed with 19 of 20 native deadlines on each boot and in the
pooled inversion panel. Both falsely accepted the segment tree on the
ordered-runs input at the merge-sort reference's own deadline. Every
native ratio for that case was above 1.15, while the two-counter score
gave 0.809. Each model estimated one of four inversion variant times
within ±20%. Relative timing error increased on all four variants with
the two-counter score.

The score is `latency_x16 + 700 × l1d_dependent`, with one unit per sixteenth
of a bound cycle and 700 per dependent L1D miss. Its weights were chosen on
the earlier predecessor and shortest-paths data. The inversion panel tests
them on previously unevaluated programs. An independent reviewer fixed this
additional comparison on November 28, 2026 at 08:37:57 UTC, after the parent
experiment had started collection and before the reviewer had inspected its
measurements. The parent study's original protocol is separate.
[Validation plan and frozen score](https://github.com/seo-rii/durante/pull/125#pullrequestreview-6096790472)
· [Inversion collection registration](https://github.com/seo-rii/durante/commit/b410405414138ea67f79d40ed7d8a6db1541aa3c)

## Verdicts and relative-time error

| Inversion input and variant | Native median ratio | ML4 ratio | Two-counter ratio | ML4 timing error | Two-counter timing error |
| --- | ---: | ---: | ---: | ---: | ---: |
| Shuffled, Fenwick | 0.621 | 0.838 | 0.840 | +35.05% | +35.40% |
| Shuffled, segment tree | 0.905 | 0.797 | 0.778 | −12.02% | −14.06% |
| Ordered runs, Fenwick | 0.706 | 0.869 | 0.873 | +22.99% | +23.62% |
| Ordered runs, segment tree | 1.181 | 0.825 | 0.809 | −30.13% | −31.56% |

Median absolute relative timing error on the holdout was 26.83% versus
27.86% on the first boot and 26.32% versus 27.34% on the second, in ML4
and two-counter order. The pooled values were 26.56% and 27.59%. There
were zero false timeouts and zero indeterminate comparisons for each model
on both individual boots and in the pooled panel.

On the remeasured predecessor development cases, ML4 matched 18 of 20
pooled verdicts and the two-counter score matched 20. Both estimated
three of four times within ±20%. These counts use the DSU reference on
each predecessor input.

The two inversion inputs each use their own registered merge-sort reference.
For a variant, the native ratio divides its median native process CPU by the
reference median. The predicted ratio uses the corresponding model scores.
A time estimate falls within ±20% when its predicted/native ratio is between
0.8 and 1.2 inclusive, before rounding. The deadline multipliers are 1, 1.25,
1.5, 2 and 3. Both native and predicted verdicts must agree over every pair
of their respective observations. Equality passes. A range crossing the
deadline is indeterminate. The pooled panel includes cross-boot pairs.
The reference supplies the scale and is excluded from the evaluation counts.

[Complete per-boot and pooled case results](results.json) include every
inversion case and the remeasured predecessor development panel separately.
The replay verifies the original measurement receipts and computes the
unmodified Dijkstra ML4 baseline from the same inputs. From this directory:

```sh
python3 -B evaluate.py --verify results.json
python3 -B -m unittest discover
```

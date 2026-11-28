# Offsets on reference-relative timing scores

The original ML4 cache score plus a 6.92658480142496 ns gshare miss penalty predicted 2.411 times KMP's time for Z on random text and 15.787 times KMP's time for the naive matcher on repetitive text. On the first pinned Milan boot, the native ranges were 1.355–1.481 and 3.944–4.471 respectively. The KMP reference had a score of only 2.24–2.28 ms across these inputs. This study tests how sensitive relative predictions are to a common additive term in that score. [Original observations](../string_matching/README.md)

## Development on literal matching

Let S and R be the submission and reference scores expressed in nanoseconds. For each alternative, input and boot, solve (S+c)/(R+c) = t, where t is the ratio of the median native CPU times on that boot. The six positive crossings ranged from 4.614 to 9.127 ms. Their median was 7.117283 ms, rounded to a 7 ms score offset. The other two crossings were negative. On both boots the random-text naive implementation was faster than KMP natively and slower in the baseline score. Adding the same nonnegative c to both scores always preserves their ordering at 1×. The fitted value describes these ratios; the experiment did not measure a 7 ms startup cost. [Registered global offset](https://github.com/seo-rii/durante/blob/13384362f13c824587b8359ed747a6f3f57f47b8/experiments/capacity_misses/score_offset/protocol.json)

The global rule uses c = 7 ms for every reference. A follow-up rule uses c = max(0, 7 ms − R). The latter gives the denominator a floor and stops shrinking ratios as soon as R reaches 7 ms. It was chosen after examining the global rule's regressions on the six older families. Its performance on these archives is exploratory. [Reference-floor registration](https://github.com/seo-rii/durante/blob/47f01b65aed66db0872e1aa4a02b2639ee7a968a/experiments/capacity_misses/score_offset/protocol.json)

| String solver and text | Original ratio | Global offset | Reference floor |
| --- | ---: | ---: | ---: |
| Z, random | 2.411 | 1.342 | 1.452 |
| Naive, random | 1.086 | 1.021 | 1.028 |
| Z, repetitive | 2.922 | 1.473 | 1.627 |
| Naive, repetitive | 15.787 | 4.636 | 5.821 |

## Transfer across problem families

Native acceptance requires every solver/reference pair on the same boot to meet the inclusive deadline. A cell with pairs on both sides is mixed. The five multipliers are 1, 1.25, 1.5, 2 and 3. Error percentages are medians of the absolute errors in solver/reference CPU ratios. The other six families are BFS, Dijkstra, Fenwick range sum, predecessor, inversions and range-distinct queries. Historical and layered Dijkstra share solver binaries and stay together. Their results and both orders of each problem weight the comparison-level medians. The two boots independently reran the same inputs. [Native and score values for every comparison](results.json)

| Programs and boot | Model | Correct / native-decisive | Median error | Ratios within ±20% |
| --- | --- | ---: | ---: | ---: |
| Matching, first | Original | 15/19 | 86.30% | 0/4 |
| | Global offset | 18/19 | 9.45% | 4/4 |
| | Reference floor | 18/19 | 14.70% | 3/4 |
| Matching, second | Original | 15/17 | 81.58% | 1/4 |
| | Global offset | 16/17 | 9.46% | 4/4 |
| | Reference floor | 16/17 | 10.90% | 3/4 |
| Other six, first | Original | 102/107 | 5.86% | 18/22 |
| | Global offset | 100/107 | 7.43% | 19/22 |
| | Reference floor | 102/107 | 5.86% | 20/22 |
| Other six, second | Original | 99/103 | 6.69% | 16/22 |
| | Global offset | 97/103 | 8.12% | 17/22 |
| | Reference floor | 99/103 | 6.69% | 18/22 |

The global offset introduced the same two false accepts on each boot. Historical set-Dijkstra fell from 1.516× to 1.488× at a 1.5× cutoff. Shuffled range-distinct Mo fell from 1.284× to 1.169× at 1.25×. The reference floor left both calls intact. It changed no deadline outcomes for the other six families; only the two once-improved layered Dijkstra alternatives had a reference score below the floor. Previously existing errors remain, including the original set-Dijkstra 2× false accept on the first boot. All wrong string decisions were false timeouts. Both adjustments still time out random-text naive at 1×.

A separate retrospective leave-one-family-out check repeats the positive-crossing fit on the remaining six families for each fold. It keeps every order and boot of the target out of the fit. Excluding matching entirely gives a 3 ms global offset. It produces 17/19 and 16/17 correct matching decisions, with median ratio errors of 22.22% and 18.31%. Across all seven folds the fitted values range from 3 to 6 ms. This check uses the same public archives on which the choice of adjustment was developed. A new workload needs an independent test with a frozen rule.

Run from this directory with Python 3.11 and NumPy:

```sh
python3 -B -m unittest discover
python3 -B replay.py --verify results.json
```

The replay reads ten original guest archives through their existing measurement validators. It applies the same registered Dijkstra ML4 weights to every family. The historical Milan panel's branch counts came from matching local ELF/input pairs, while all other branch readings, all cache counters and all native times came from the pinned guests. All scores, fitted crossings and deadline comparisons use rational arithmetic. The raw measurements and the scoring defaults remain those of their source studies.

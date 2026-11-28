# Literal string matching at relative time limits

The previously calibrated time interval **failed its registered criterion on both boots**. Each boot issued one correct accept and nine correct timeouts. The interval also timed out several runs that were within their native limit, and issued decisions when native timings spanned the limit.

| Independent boot | Correct accepts | Correct timeouts | False timeouts | False accepts | Issued on mixed native timings | Abstentions |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Primary | 1 | 9 | 4 | 0 | 1 | 5 |
| Repeat | 1 | 9 | 2 | 0 | 3 | 5 |

Counts include all 20 thresholds per boot. Repetitions, thresholds and boots describe four solver/text comparisons on one problem. The prior [layered Dijkstra study](../relative_intervals/) issued only accepts using these historical multipliers. This new study produced both decision directions, including wrong timeouts.

## Where the relative score overestimates CPU

| Alternative and text | Primary native ratio to KMP, all pairs | Repeat native ratio, all pairs | Unadjusted model ratio |
| --- | ---: | ---: | ---: |
| Z, random | 1.355–1.481 | 1.366–1.565 | 2.411 |
| Naive, random | 0.835–0.909 | 0.830–0.952 | 1.086 |
| Z, periodic | 1.414–1.522 | 1.367–2.438 | 2.922 |
| Naive, periodic | 3.944–4.471 | 3.714–4.203 | 15.787 |

The lower endpoint for random-text Z is 2.242 times KMP after applying the historical multiplier. The interval therefore times out Z at the 2× limit, although every native random-text Z reading passed on both boots. By contrast, the naive matcher on the periodic text exceeded even the largest registered limit (3×) natively. Its widely overestimated time still yields the correct five timeout decisions.

An exploratory decomposition in the replay attributes each term’s difference from KMP to the original full KMP score. Of the 1.411 excess in the random-text Z score, 0.861 comes from streaming 2 MiB cache misses and 0.544 from the static latency estimate. Removing the streaming term from both sides gives 1.718×. Of the 14.787 excess for periodic naive, 11.607 comes from static latency and 3.180 from simulated branch mispredictions. Removing the branch term still gives 12.710×. These arithmetic differences describe the score; they do not measure hardware penalties for individual operations. All registered decisions above retain every original term.

## Registration and reproduction

The task counts overlapping matches of a nonempty literal pattern. KMP is the independent reference. The two alternative submissions use the Z algorithm and an early-exit character loop at each starting position. All three share the input and output code. A Python oracle using repeated literal search verifies each output, including a separate overlapping-pattern control.

Both texts contain 1,048,576 bytes. They use the same 48-byte pattern, consisting of 47 `a` bytes ending in `b`, and have 128 exact matches. The random background draws uniformly from a sixteen-character alphabet using a seeded xorshift32 generator. The periodic background uses `a` everywhere except the planted occurrences. Both place the pattern at the same offsets. The naive solver performs 1,262,788 character comparisons on the random input and 50,185,008 on the periodic input, a 39.74-fold difference in comparisons. This operation count does not include startup, I/O, branch costs or other instructions.

The [prior study](../relative_intervals/) calibrated the two multipliers on the predecessor, inversion, range-distinct and historical Dijkstra archives. This study uses those prior multipliers exactly, with the same ML4 score plus 6.92658480142496 ns per simulated gshare misprediction. The model and multipliers were fixed before measuring these texts. The two texts, compiler options, executable hashes, expected outputs, thresholds, outcome categories and the success criterion were [registered before collection](https://github.com/seo-rii/durante/commit/a53ace8e962011b692a5f86beca4c30711f9d961).

Each submission and text ran seven times natively and twice per original-cache, flat-cache and branch configuration per independent GCE Milan VM. The reverse boot reversed the entire schedule. Each process had affinity to CPU 1 with one thread per physical core. The model bounds include all cross-pairings of unrounded submission and reference scores. Multiply their endpoints by the historical factors before comparing against the five inclusive limits (1, 1.25, 1.5, 2 and 3 times the reference). The raw native bounds use child user plus system CPU independently for each boot. The full [case-level results](results.json) include exact rational endpoints and each verdict.

Run `python3 -B -m unittest` and `python3 -B replay.py --verify results.json` from this directory. The replay verifies the manifests and source hashes, regenerates the inputs and oracle answers, validates every run’s collector data, CPU placement and exit status, and checks the teardown receipts for both private VMs and disks. To rebuild the measurement payload, run `prepare.py --registered-tools PATH --output NEW_DIR` with the unpacked [range-distinct](../range_distinct/) tools. [Registration](registration.json) · [Protocol](protocol.json) · [Guest receipts](evidence/cloud.json)

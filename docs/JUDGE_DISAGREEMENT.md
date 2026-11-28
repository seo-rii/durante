# How often a work clock and a CPU-time judge disagree

A judge that converts work counts to virtual milliseconds gives a different
verdict from an ordinary CPU-time judge whenever the conversion error moves a
submission across the time limit. This study measures how often that happens
for realistic submissions: 49 correct solutions to the seven
[contest problems](CONTEST_CORPUS.md) that differ in how they spend time, judged
against limits set from each problem's intended solution.

On one 4-vCPU Intel Xeon guest, an instruction-and-REP model (M0)
disagreed with native CPU time on 12-17% of decisions under a limit of 1.5-3x
the intended solution set with the same clock (12-21% when the limit is
compared in predicted seconds). Most disagreements were **false accepts**: the work clock
passed programs whose native CPU time exceeded the limit. They concentrate in
pointer-based containers, cache-hostile traversal and per-line system calls.

## Submissions

`experiments/judge_disagreement/variants` holds seven variants per problem.
Six were marked in range and one was marked too slow per problem. On the
round-2 guest the in-range variants ran at 0.69–13.87 times the intended
solution's native median, and the seven too-slow variants at 17.34–42.90 times.
The manifest separately records preliminary full-tier timings. The submissions
vary in their choice of `std::map`, `std::set`, `unordered_map`,
`std::list` or a pbds tree instead of arrays; pointer-linked segment trees;
column-major tables; recursion instead of an explicit stack; synced `cin`,
`endl` after every answer; worse complexity classes (sqrt decomposition,
Bellman-Ford, Hunt-Szymanski); bit-parallel and branch-free rewrites.
`variants/check.py` compiles every program with the contest flags and checks its
output against the problem's reference on both input tiers.

## Method

The counters, native timings and models are those of round 2 of the
[capacity-miss study](CAPACITY_MISSES.md). The archived raw file identifies
the comparison plan and rule by SHA-256. The plan and the results first appear
together in the same upstream commit. [Original study commit](https://github.com/seo-rii/durante/commit/a34ad3976e0fc38ec512e934e5df8ac10c030bad)

M0 fits separate nonnegative coefficients for
instructions and REP bytes on this guest. All model verdicts below are
reconstructed from completed executions. For problem p and k in {1.5, 2, 3}
the native limit is k times the
intended solution's native median CPU time. A variant's native verdict is AC
when its median is within the limit. When the limit lies between its fastest
and slowest native runs the decision is ambiguous and excluded (6-7 of 49 per
setting). Each model is refitted without problem p's intended solution, so no
program of p is seen by its weights.

- **Absolute (A).** The model's predicted CPU seconds are compared with the
  native limit.
- **Relative (R).** The setter measures the intended solution with the same work
  clock and sets the virtual limit to k times its virtual time; a variant passes
  when its virtual time is within that. This is how a judge that sets limits in
  virtual milliseconds would work, and it cancels the conversion error shared
  by both programs.

## Results

Disagreements over unambiguous decisions (false TLE / false AC):

| Model | R, k = 1.5 | R, k = 2 | R, k = 3 | A, k = 1.5 | A, k = 2 | A, k = 3 |
|---|---|---|---|---|---|---|
| M0 instructions + REP | 7/42 (0/7) | 6/42 (1/5) | 5/43 (2/3) | 9/42 (0/9) | 5/42 (0/5) | 7/43 (1/6) |
| ML latency bound + REP | 6/42 (0/6) | 5/42 (1/4) | 4/43 (1/3) | 16/42 (0/16) | 14/42 (0/14) | 11/43 (0/11) |
| M1 + L1D misses | 5/42 (0/5) | 4/42 (1/3) | 5/43 (2/3) | 9/42 (0/9) | 6/42 (0/6) | 7/43 (0/7) |
| M2 + L1D and capacity misses | 3/42 (0/3) | 4/42 (1/3) | 5/43 (2/3) | 9/42 (0/9) | 6/42 (0/6) | 5/43 (1/4) |
| M4 + dependent misses | 4/42 (1/3) | 6/42 (2/4) | 3/43 (1/2) | 6/42 (1/5) | 5/42 (0/5) | 4/43 (1/3) |
| ML4 latency bound + REP and misses | 4/42 (1/3) | 4/42 (2/2) | 5/43 (2/3) | 12/42 (0/12) | 10/42 (0/10) | 8/43 (1/7) |

With M0 and a relative limit, 6 of 42 decisions (14%) at k = 2 differ from
native judging. The five false accepts came from the three DFS submissions using
set, hash-map or multimap adjacency, the column-major LCS table and the
order-statistics tree. The list-based BFS queue received the false timeout.
In the manifest's pointer-chasing class alone, two of four unambiguous decisions
were false accepts and another two were ambiguous. The scorer exports
unambiguous counts, ambiguous counts and both error directions for every
model, policy, factor and manifest class under `verdicts_by_class`. At this
setting, the two primary candidates M4 and ML4 disagreed on six and four
decisions. Both missed the study's separate accuracy criterion on the five
stable in-range variants.

Across all six settings, M0 produced false accepts for 14 distinct variants
and false timeouts for three.

- **False accepts.**
  - BFS with a hash map for distances, DFS with set, hash-map, multimap or
    map-of-sets adjacency, Dijkstra with map adjacency and hash-map distances,
    Dijkstra with a `std::set` priority queue, the pbds order-statistics tree
    and `std::list::sort`.
  - A column-major LCS table and the Hunt–Szymanski LCS implementation.
  - A recursive DFS with `scanf`.
  - A range-update Fenwick tree that does twice the intended work.
  - `cin` with `endl` (one `write(2)` per answer, which the counters cannot see).
- **False rejects.** A `std::list` BFS queue, a heap-based BFS and a sqrt
  decomposition, whose instructions are cheap per unit.

Adding capacity misses (M2) removed some of the k = 1.5 false accepts (7 to 3)
but not the container cases at k = 2 and 3. The dependent-load counter (M4)
only follows chains within one block, so tree descents keep their error (see
[the capacity study](CAPACITY_MISSES.md)). The latency-bound clock (ML) is
closer to native verdicts with relative limits than with absolute limits on
this guest. All six models rejected every too-slow variant under relative
limits. Under absolute limits, the map-of-sets DFS (17.34x its intended
solution's native median) was falsely accepted by ML at all three factors and
by M0, M1 and ML4 at k = 3. M2 and M4 rejected all seven under every setting.

## What this means for judging

- On this corpus, a work-clock verdict near the limit is unreliable for
  solutions whose cost is memory latency or kernel time. The clock is lenient:
  a contestant can pass with a slower container or I/O pattern than a CPU-time
  judge would allow.
- For ML, setting the limit relative to the intended solution reduced the
  counts from 16, 14 and 11 to 6, 5 and 4 across the three factors. For M0 at
  k = 2 the corresponding counts were five absolute and six relative.
- At a relative limit of 3x, each model disagreed on 3–5 of 43 decisions
  (7–12%). As k increased, the M0 counts fell from seven to six to five,
  while the M2 counts grew from three to four to five.
- Before deciding verdicts with the work clock, collect shadow comparisons on
  real submissions (Aonohako's `AONOHAKO_DURANTE_JUDGE_MODE=shadow` and
  `cmd/durante-shadow-summary`). Read the rates per solution class, not only
  overall.

## Limits

One guest, seven problems and 49 variants authored for this study. Each variant
had 15 native rounds and one counter observation. The median native spread
among these variants was 31% while other jobs ran on the guest. Intended
solution spreads ranged from 30% to 58%. The denominator excludes variants
whose observed native times straddle the fixed limit. Uncertainty in the
intended solution's median can also move that limit. Absolute timing models
used 36 calibration cases from the same guest for the accuracy study and 35
for each problem's verdict comparison.

```sh
python3 -B experiments/judge_disagreement/variants/check.py
python3 -B experiments/capacity_misses/analyze_v2.py experiments/capacity_misses/results/v2/results.json --output /tmp/durante-v2-scores.json
```

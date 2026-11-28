# How often a work clock and a CPU-time judge disagree

A judge that converts work counts to virtual milliseconds gives a different
verdict from an ordinary CPU-time judge whenever the conversion error moves a
submission across the time limit. This study measures how often that happens
for realistic submissions: 49 correct solutions to the seven
[contest problems](CONTEST_CORPUS.md) that differ in how they spend time, judged
against limits set from each problem's intended solution.

On one 4-vCPU Intel Xeon guest, the ordinary instruction-and-REP clock (M0)
disagreed with native CPU time on 12-17% of decisions under a limit of 1.5-3x
the intended solution set with the same clock (12-21% when the limit is
compared in predicted seconds). Most disagreements were **false accepts**: the work clock
passed programs whose native CPU time exceeded the limit. They concentrate in
pointer-based containers, cache-hostile traversal and per-line system calls.

## Submissions

`experiments/judge_disagreement/variants` holds seven variants per problem
(manifest.json describes each). Six run within about 15x of the intended
`tests/contest/<problem>/sol.cpp` and one is too slow (19-49x). They differ the
way contestants' solutions do: `std::map`, `std::set`, `unordered_map`,
`std::list` or a pbds tree instead of arrays; pointer-linked segment trees;
column-major tables; recursion instead of an explicit stack; synced `cin`,
`endl` after every answer; worse complexity classes (sqrt decomposition,
Bellman-Ford, Hunt-Szymanski); bit-parallel and branch-free rewrites.
`variants/check.py` compiles every program with the contest flags and checks its
output against the problem's reference on both input tiers.

## Method

The counters, native timings and models are those of round 2 of the
[capacity-miss study](CAPACITY_MISSES.md), whose predeclaration also fixed this
analysis. For problem p and k in {1.5, 2, 3} the native limit is k times the
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
| M0 instructions + REP (ordinary clock) | 7/42 (0/7) | 6/42 (1/5) | 5/43 (2/3) | 9/42 (0/9) | 5/42 (0/5) | 7/43 (1/6) |
| ML latency bound + REP | 6/42 (0/6) | 5/42 (1/4) | 4/43 (1/3) | 16/42 (0/16) | 14/42 (0/14) | 11/43 (0/11) |
| M2 + L1D and capacity misses | 3/42 (0/3) | 4/42 (1/3) | 5/43 (2/3) | 9/42 (0/9) | 6/42 (0/6) | 5/43 (1/4) |
| M4 + dependent misses | 4/42 (1/3) | 6/42 (2/4) | 3/43 (1/2) | 6/42 (1/5) | 5/42 (0/5) | 4/43 (1/3) |

With the ordinary clock and a relative limit, 6 of 42 decisions (14%) at k = 2
differ from native judging. Across all six settings, the misjudged variants
under M0 were:

- **False accepts.**
  - Pointer-based and hashed containers: the `std::set`, `unordered_map` and
    `multimap` adjacency DFS, the pbds order-statistics tree, a `std::set`
    priority queue and `std::list::sort`.
  - A column-major table.
  - A recursive DFS with `scanf`.
  - A range-update Fenwick tree that does twice the intended work.
  - `cin` with `endl` (one `write(2)` per answer, which the counters cannot see).
- **False rejects.** A `std::list` BFS queue, a heap-based BFS and a sqrt
  decomposition, whose instructions are cheap per unit.

Adding capacity misses (M2) removed some of the k = 1.5 false accepts (7 to 3)
but not the container cases at k = 2 and 3. The dependent-load counter (M4)
only follows chains within one block, so tree descents keep their error (see
[the capacity study](CAPACITY_MISSES.md)). The latency-bound clock (ML) is
useful only relatively: in absolute terms it underpredicts most programs on this
guest. The too-slow variants were rejected by every model, with one exception:
M0 accepted the slowest DFS (20x) under the absolute limit at k = 3.

## What this means for judging

- On this corpus, a work-clock verdict near the limit is unreliable for
  solutions whose cost is memory latency or kernel time. The clock is lenient:
  a contestant can pass with a slower container or I/O pattern than a CPU-time
  judge would allow.
- Setting the limit relative to the intended solution under the same clock
  (policy R) halves the disagreements of the absolute policy for the latency
  clock, and is the right way to set virtual limits. It does not remove the
  container cases.
- Disagreements fall with larger k but stay at about 7-12% even at 3x.
- Before deciding verdicts with the work clock, collect shadow comparisons on
  real submissions (Aonohako's `AONOHAKO_DURANTE_JUDGE_MODE=shadow` and
  `cmd/durante-shadow-summary`). Read the rates per solution class, not only
  overall.

## Limits

One guest, seven problems and 49 variants. Native medians came from 15 rounds
with a median spread of 31% while other jobs ran on the guest; ambiguous
decisions were excluded rather than repeated. The variants were written for this
study rather than collected from contests. The models were fitted on 36
calibration cases from the same guest.

```sh
python3 -B experiments/judge_disagreement/variants/check.py
python3 -B experiments/capacity_misses/analyze_v2.py experiments/capacity_misses/results/v2/results.json
```

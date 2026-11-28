# Can call counts recover the set-Dijkstra verdict?

The four separately counted instruction classes include a signal that clears
the previous 1.768× ceiling. The set solver executes **2.295× as many direct
calls** as the intended heap solver on the same #114 graph. But every single
nonnegative event coefficient that fixes the 2× verdict breaks another
previously correct contest verdict. All five candidates fail the selection criterion for grading. [Earlier ceiling](https://github.com/seo-rii/durante/pull/117)

## What the counters measure

At translation the sidecar tallies direct calls, indirect calls, returns, and
integer DIV/IDIV in each basic block. Inline adds at block entry charge those
counts on execution. It also counts total instructions as an integrity control.
The client uses no per-branch callbacks, cache simulation, symbol names or
operand values. The previous Rust instruction-mix probe groups direct calls
with unconditional jumps, and groups indirect calls with other indirect
branches. Its DIV/IDIV definition is reused. The runtime overhead of the sidecar and of a production integration still
requires measurement. [Instruction-mix source](../../time_features/featclient.c)

We committed the five candidate features (including total calls), schedule,
coefficient rule and transfer check before collecting the new event counts.
Two local single-thread runs of every registered #114 and #116 ELF used CPU 6,
the original stdin and x86-64-v2 CPUID profile. Every stdout matched its
registered hash. All four event counts matched across repeats on all 16 ELFs;
the sidecar's instruction total matched the sealed client in each run.
A freestanding assembly fixture on each runtime exercised both integer divides
and all three control-flow classes. [Protocol](protocol.md)

The CPU time references are the original 7 native samples per solver on
each of the two Milan boots **per panel**. The event counts are local.
Compared with the archived guest, the largest instruction count differences
were 0.31 ppm on #114 and 434.93 ppm on #116. Local and guest user-space library paths may differ. The coefficient measures
a model correction across submissions and has units of ns/event.

| #114 Dijkstra solver | Direct calls | Indirect calls | Returns | Integer divides |
|---|---:|---:|---:|---:|
| Intended heap | 2,025,633 | 317 | 2,025,945 | 193 |
| Alternate heap | 2,722,104 | 5,932 | 2,728,031 | 9 |
| Ordered set | 4,648,221 | 5,932 | 4,654,148 | 9 |

The 18.7× indirect-call ratio comes from both alternate implementations.
Indirect calls cannot distinguish them from each other on this input.
For #116 the ordered set makes 955,378 direct calls and the reference heap
640,791 (1.491×), with exactly 223 indirect calls in both. All 16 solver
counts and the two-run ranges are in [the replay output](results.json).

## Single-coefficient feasibility

For each candidate independently, add `k × event_count` ns to the frozen
ML4 score **on both sides**. For #114 use the original per-problem weights,
intended solver and 1.5×, 2× and 3× deadlines. For #116 multiply the score
ratio by the native time of its original reference solver and use 65 ms.
The replay requires each clear native verdict to stay clear with the same
label across every cross-pair of observed endpoints, separately for each
boot and for their union. The primary set-2× native verdict was clear. The
repeat and pooled native bounds overlapped the deadline. The baseline had
one primary false acceptance on #114 and agreed on all six #116 solvers.

| Added event | Set/intended ratio | k needed by primary set-2× | Another decisive #114 constraint | Largest k preserving every #116 verdict |
|---|---:|---:|---:|---:|
| Direct calls | 2.295× | >77.90632 | ≤1.64127 (list-queue BFS, 2×) | <9.88296 |
| Indirect calls | 18.713× | >8,778.13599 | <27.29092 (range-BIT Fenwick, 2×) | <29,368.96029 |
| Returns | 2.297× | >77.22033 | ≤1.64146 (list-queue BFS, 2×) | <9.87971 |
| Integer divides | 0.047× | infeasible for k ≥ 0 | — | <8.80264 |
| All calls | 2.297× | >77.22098 | ≤1.64146 (list-queue BFS, 2×) | <9.87963 |

Units are ns/event, boundaries rounded for display. The exact rational
inequalities drive the replay. For example, frozen set ML4 is 46.507 ms
below twice the heap score. The set executes 596,955 more direct calls than
twice the heap. Thus closing this discrepancy needs more than 77.906 ns per
direct call while preserving BFS places a much lower ceiling on the same k.
On #116 the limiting case is block4 on the repeat boot. Adding a common
term can reduce a variant's estimated time after reference normalization.

No candidate survived selection on #114. As a post-hoc illustration,
78 ns per direct call flips the set case to timeout, but reduces primary
#114 agreement from 16/18 to 10/18 (five false timeouts, two false accepts,
one uncertain). It also falsely accepts #116 block4 on both boots.
At 8,779 ns per indirect call, #116 retains six agreements per boot but
#114 primary has three false accepts. These examples use the smallest
integer coefficients clearing the single target. The verdicts concern only these inputs, the
fixed ML4 weights, and the five one-feature extensions tested here.

## Reproduction

From the repository root, replay the committed process receipts and four
pinned guest archives. The optional `--cells` report includes each solver,
deadline, native verdict and its allowed coefficient interval.

```sh
study=experiments/capacity_misses/block_events
python3 "$study/replay.py" --verify
python3 "$study/replay.py" --cells
python3 -m unittest discover -s "$study" -p test_events.py
```

To recollect the event counts, supply the two registered guest payload
directories from the parent studies and the DynamoRIO 11.3.0 SDK.
The collector makes a private copy of the patched guest runtime and adds
the SDK drx and drreg extensions. It checks the original registered
runtime, client, plan, ELF, and input digests and runs the two local orders.
Choose an available CPU and a fresh output path.

```sh
cmake -S "$study" -B /tmp/block-events-build -DCMAKE_BUILD_TYPE=Release -DDynamoRIO_DIR="$sdk/cmake"
cmake --build /tmp/block-events-build
cc -nostdlib -static -Wl,--build-id=none "$study/counter_check.S" -o /tmp/block-events-check
python3 "$study/collect_events.py" --milan "$milan_payload" --paths "$paths_payload" \
  --sdk "$sdk" --client /tmp/block-events-build/libblock_events.so \
  --control /tmp/block-events-check --cpu 6 --out /tmp/block-events-local.json.gz
```

[Contest evidence](../milan/README.md) · [Shortest-paths evidence](../aonohako_shortest_paths/README.md)

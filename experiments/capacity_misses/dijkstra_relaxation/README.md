# Dijkstra with one or 24 tentative improvements per target

The registered directional prediction failed on both boots. Giving each target 24 tentative distances raised the ratio of ordered-set CPU to alternate-heap CPU from **1.104× to 1.216×** in the pooled medians. The same reader and adjacency construction are used on both sides. The center-to-target updates incur search, erase and insertion in the ordered set while growing the lazy heap with stale entries. This experiment measures the complete implementations; its timing cannot assign the difference to individual operations.

| Set / alternate heap | One improvement | 24 improvements |
| --- | ---: | ---: |
| Primary native median | 1.149 | 1.213 |
| Repeat native median | 1.113 | 1.224 |
| Pooled native median | 1.104 | 1.216 |
| ML4 | 1.083 | 1.561 |
| ML4 + 6.9266 ns / gshare miss | 1.054 | 1.144 |
| Latency + 700 × dependent L1D misses | 1.075 | 2.192 |
| ML4 + 3.3 ns / gshare miss | 1.068 | 1.300 |

The full native observation ranges overlap between the two schedules. Each median supports the direction shown in the table, while the individual readings allow the opposite ordering.

## Time limits against the original contest reference

Each graph supplies two alternatives to the original reference ELF and five inclusive limits (1, 1.25, 1.5, 2, 3 times the reference). Of the 20 pooled comparisons, the native readings agreed on 19. The alternate heap on the one-improvement graph ranged from 0.577 to 1.045 times reference across all pairs. Keeping that mixed 1× cell visible gives the following results.

| Frozen relative model | Correct / 19 decisive | False timeouts | Timing ratios within ±20% | Median absolute ratio error |
| --- | ---: | ---: | ---: | ---: |
| ML4 | 18 | 1 | 3/4 | 7.91% |
| ML4 + 6.9266 ns / gshare miss | 19 | 0 | 2/4 | 13.25% |
| Latency + 700 × dependent L1D misses | 16 | 3 | 3/4 | 13.04% |
| ML4 + 3.3 ns / gshare miss | 19 | 0 | 4/4 | 11.40% |

The 6.9266 ns addition fixes the many-improvements set at 1.25× yet pushes both one-improvement timing ratios beyond ±20%. The 3.3 ns addition fixes that same decision and keeps all four time ratios within ±20%; its median error still exceeds ML4’s. Neither branch model made a false accept on this input pair. On the earlier [predecessor](../order_statistics_transfer/) and [range-distinct](../range_distinct/) panels, such false accepts occurred.

Only the many-improvements set crossed the registered 1% skipped-operand threshold (1.904%). Withholding its five decisions leaves 14 correct native-decisive cells and one mixed native cell for each model; this excludes the baseline’s one false timeout and also excludes five correct decisions from each branch-extended model. The flat-cache and branch measurements together cost 69–173 times native CPU in these cases. The model comparisons are offline measurements on two weight schedules of one topology.

## Graphs and registered comparisons

Both weighted DAGs contain 6,169 vertices and 147,480 directed edges, encoded in 1,990,276 bytes. The source first reaches 24 centers at increasing, distinct distances. Every center has an edge to each of 6,144 targets. The `once` graph gives every target its shortest tentative distance on the first visit. The `many` graph improves the distance on all 24 visits. Endpoint order, destination distances and the expected output are identical. Different weight digits, queue histories and memory access patterns remain part of the comparison.

The intended executable (`ref`) uses a binary heap, buffered input and CSR adjacency. The alternate lazy heap and ordered-set executable share a reader and adjacency construction. On `many` the alternate heap drains 23 superseded entries per target. The ordered set erases and reinserts on 23 improvements per target. We registered the prediction that the ordered set’s native CPU time relative to the alternate heap would decrease. Each ELF is byte-identical to its corrected Milan counterpart. The graph, outputs, ELFs, tool hashes, scoring rules and run order were [frozen before collection](https://github.com/seo-rii/durante/commit/1c4c3018964f717c4fac8098613af29b32ce927a).

On each newly booted GCE n2d-standard-4 instance, every program and graph ran seven times natively and twice each in the original cache, flat cache and branch modes. Child user plus system CPU was measured with affinity fixed to logical CPU 1 and one thread per physical core. The repeat boot reversed the entire execution order. Every run had to exit normally and produce the independently derived answer. Guest identity, schedule, executable trace, counters, archives and VM cleanup are checked by the replay.

## Reproduction

Run `python3 -B -m unittest` and `python3 -B replay.py --verify results.json` in this directory. To rebuild the registration, supply `prepare.py` with `--registered-milan` and `--registered-tools` pointing to the unpacked [Milan](../milan/) and [range-distinct](../range_distinct/) packages, plus `--output` for a new destination. The preparer verifies registered file hashes, recreates both inputs, runs all six combinations against the mathematical oracle and packages the input to the measurement VMs. [Protocol](protocol.json) · [Registration](registration.json) · [Results](results.json)

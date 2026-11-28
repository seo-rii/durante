# Why the 2× clock accepts set-based Dijkstra

On the primary #114 Milan boot the set implementation used **2.101–2.449×** the native CPU of the intended Dijkstra on the same graph (all 49 pairs of seven runs). At the same 2× limit the frozen ML4 score was **1.523×**. The repeat boot spans 1.944–3.288×, leaving its native decision indeterminate. The probe counters for each executable matched across all four runs.

## Where the difference appears

The [intended solution](../../../../tests/contest/dijkstra/sol.cpp) uses buffered input, compressed sparse row adjacency and a binary heap. The [alternate heap](../../../judge_disagreement/variants/dijkstra/vecvec_getchar_pq.cpp) and the [ordered set](../../../judge_disagreement/variants/dijkstra/set_decrease_key.cpp) use the same reader and adjacency-list construction. Their queue and visited-state handling differ. This intermediate solver distinguishes the common representation change from the change in priority queue. Attributing time to input parsing, allocation, branch prediction or memory latency individually would require separate measurements.

| Numerator / denominator | Primary native median ratio | Repeat native median ratio | Frozen ML4 ratio |
|---|---:|---:|---:|
| Alternate heap / intended | 1.566 | 1.838 | 1.380 |
| Ordered set / alternate heap | 1.467 | 1.367 | 1.104 |
| Ordered set / intended | 2.296 | 2.512 | 1.523 |

The frozen model underestimates both primary differences. Dependent 2 MiB misses contribute 51.7% of the intended score and grow by 1.764× from intended to set. The static latency feature contributes 25.1% and grows by 1.382×. Dependent L1D misses contribute 9.5% and actually decrease to 0.806×. Moving from the alternate heap to the set increases static latency by only 1.064× and dependent 2 MiB misses by 1.100×. [All term contributions and CPU ranges](results.json)

## Which coefficients could help?

For nonnegative feature weights, the ratio of the weighted sum for the set to the same weighted sum for the intended heap is a weighted average of the per-feature ratios. Over every recorded reading, the largest ratio of the union of the four published models' features is **373/211 = 1.76777** for the 32 MiB non-streaming misses. Adding nonnegative reference counts, touched cache lines, an intercept, or the measured memory-operand byte feature preserves that bound. Therefore no nonnegative refit of these features can give the set the required ratio above 2. This result is specific to these observed counters and this pair of programs.

We separately enabled Durante's existing `-memory_operand_bytes` mode on the three registered ELFs/inputs and the original client and DynamoRIO hashes. The client charged 1.547× as many operand bytes for the set as for the intended solution, or 1.232× as many as for the alternate heap. Three local runs per mode had identical common counters, within 0.31 ppm of GCE. Enabling operand bytes cost **7.07–10.40×** the latency-plus-REP-only instrumented process CPU on this build and local host (three interleaved runs per mode, no cache simulation). The client disables its flat fast path when operand bytes are requested. The CPU ratios describe this particular client and host. [Client](../../../../client/durante.c) · [Local receipts](local-operands.json.gz)

The L1D proxy's *skipped* field is large for the set (5,382,916 vs 1,414 for the intended). It counts unsupported references, including FS/GS operands; it has no interpretation as a hardware miss penalty. As a post-hoc check, keep each original ML4 coefficient fixed and append a single shared cost per skipped reference, charging the matching anchor as well. Fixing the primary set 2× timeout requires **more than 8.644 ns** per skipped reference. Preserving the BFS list-queue's primary 2× acceptance, against its own BFS anchor, requires **at most 2.341 ns**. A single additive charge cannot meet both requirements. [Probe semantics](../../../l1d_probe/probe.h)

A second post-hoc check allows a different cost for each problem. Keep its eight original ML4 weights fixed, and minimize the original relative squared error over the 35 archived Xeon calibration cases for one additional nonnegative coefficient. This trains on none of the Milan cases. The resulting Dijkstra coefficient of 6.872 ns still falsely accepts the set at 2×. The BFS coefficient of 6.817 ns adds a false timeout for the list queue at 2× on both boots. Primary disagreements grow from 1/17 determinate to 2/17, repeat from 0/12 to 1/12 and pooled from 0/12 to 1/12. The feature choice followed inspection of the target error; these transfer results are exploratory.

Two other archives bound the scope of this conclusion. The same three sources, input bytes and expected answer on the earlier Xeon had a native set/intended range of 1.463–3.111×. That archive used the earlier probe and different binaries. On the independent Aonohako graph of #116 the ordered-set solver took 1.388–1.642× the native CPU of **that graph's own heap reference**. Its 65 ms native and ML4 results both passed. The native effect depends on the graph and its reference.

## Replay

Run `python3 -B replay.py` from this directory to verify both archived Milan studies, reparse the local process records, and reproduce [results.json](results.json). Run `python3 -B -m unittest -v test_gap` for interval and zero-column cases. To collect new optional-counter readings, run `python3 -B operands.py --payload PATH --cpu N --out NEW.json.gz` with the unpacked [registered Milan payload](../README.md). The collector checks the executable, stdin, runtime, CPUID setting and stdout for every run. The clock under study, its coefficients and its judging configuration were unchanged throughout.

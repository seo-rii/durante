# L1 data-cache probe

This Linux x86-64 experiment counts demand accesses to a cold 32 KiB virtual-address cache with 64-byte lines, 64 sets, eight ways, write allocation and exact LRU replacement. It produces aggregate counts alongside the usual Durante work report. The Aonohako judging clock uses its existing instruction and REP counters.

## Build and check

From the repository root, prepare the pinned SDK and use its patched runtime. The model test compares every tag's LRU position and all counters to an independent per-element implementation. One assembly control checks registers, flags, read-modify-write order and overlapping backward copies against a hand-counted trace. A second control reads the live work page during execution, on both sides of the 4,096-instruction publication threshold, after REP and after a syscall.

```sh
python3 toolkit/fetch_sdk.py
sdk="$PWD/toolkit/sdk/DynamoRIO-Linux-11.3.0-1"
cmake -S experiments/l1d_probe -B build/l1d -DCMAKE_BUILD_TYPE=Release \
  -DDynamoRIO_DIR="$sdk/cmake" -DDURANTE_TEST_DRRUN="$sdk/bin64/drrun"
cmake --build build/l1d -j2
ctest --test-dir build/l1d --output-on-failure
```

## Interpreting a measurement

Use a trusted, single-threaded static executable with an uninterrupted execution and a single process image. The probe counts attempted **decoded application operands** in read-then-write order. A reference spanning two lines adds two line lookups. A same-address read-modify-write adds two references. REP MOVS reads the source before writing the destination for every element, and REP STOS writes each destination. The probe groups accesses to the same lines whenever they leave the cache in the same state as element-by-element replay.

The stderr line beginning with `PROBE_L1D` gives operand references (`refs`), line lookups (`lines`), misses, skipped operands and the number of supported REP instructions executed. Record `skipped` alongside misses. FS/GS-relative operands, vector-indexed addresses, unknown or oversized widths and unsupported string operations contribute to `skipped`. Instruction fetch, prefetch, kernel accesses, speculative execution and coherence are outside this model. Absolute miss counts can depend on application address layout.

For ordinary reads, the generated code checks whether the whole operand fits in the most recently used line of its set. A hit leaves that set in the correct LRU order and returns directly to the application. Other reads use the full cache model. The probe counts the first line and reference of each such read together at the basic block boundary.

An ordinary store shares its callback with Durante's write checkpoint. A read followed immediately by a write to the same operand shares one LRU update and counts both references. XLAT uses the low byte of RAX for its index, including with a 32-bit address override. For long contiguous REP STOS operations the model checks the initial cache contents for hits and reconstructs the final LRU order from the range. Its work stays bounded by the number of lines in the simulated cache.

With the single-thread cache probe enabled, ordinary basic blocks can also charge instructions inline between live clock publications. REP or a replay returns the following block to the regular clock routine. To keep setup cheap for short programs, most blocks first translated during the first 131,072 instructions use compact callbacks. A block that branches to its own entry uses inline instrumentation immediately. Both layouts share the same cache and live clock state. The `--include-off` benchmark arm uses Durante's ordinary block accounting with the probe disabled. Its timings therefore compare the two complete measurement configurations.

## Compare clients

Use commit `3a77fdb` to compare against the preceding probe, or `98bda4890994` for the original scalar reference that simulates every REP element individually.

```sh
git worktree add --detach ../durante-l1d-reference 3a77fdb
cmake -S ../durante-l1d-reference/experiments/l1d_probe -B build/l1d-reference \
  -DCMAKE_BUILD_TYPE=Release -DDynamoRIO_DIR="$sdk/cmake"
cmake --build build/l1d-reference -j2
old_client="$PWD/build/l1d-reference/libdurante_l1d.so"
```

`compare.py` takes a static executable, stdin file and expected stdout file per case. It pins executions to one available CPU, disables address randomization, uses matching paths and alternates the clients. With `--include-off` it rotates all three configurations, including the optimized client with cache simulation disabled. Each run must reproduce the expected output, all terminal work fields and the live counters, and both enabled clients must match every reported cache field. CPU seconds include the measured process tree's user and system time. The separate wall times also include scheduling delays.

```sh
python3 -B experiments/l1d_probe/compare.py \
  --drrun "$sdk/bin64/drrun" --baseline "$old_client" \
  --candidate "$PWD/build/l1d/libdurante_l1d.so" --include-off \
  --cpu "$cpu" --rounds 3 --output "$results" \
  --case example "$program" "$input" "$expected_stdout"
```

Use an unused output directory. Its `summary.json` includes executable and input hashes, per-run counters, CPU and elapsed times, and the median and range. The machine needs Python 3.11 or newer, GNU timeout, taskset and permission to use `setarch x86_64 -R`.

## Long disjoint copies

A long REP MOVS between disjoint cache-line ranges now handles its middle in constant time. This applies when source and destination start at the same offset within a line, the elements are naturally aligned and neither range wraps around the effective address space. The direction flag may select either traversal direction. The first 512 line pairs evict every preexisting cache tag. Each later pair therefore misses twice. Processing the last 512 pairs reconstructs the final LRU order in every set, including when the streams compete for the same set. Other copies use the existing per-line or per-element path.

On the three static CPP20 copy inputs the median child CPU for the complete diagnostic fell by 46.7%, 49.0% and 60.8%. At the largest size it fell from 1.703 s to 0.668 s, compared with 0.467 s with simulation disabled. On the three held-out algorithms the optimized cache-enabled meter still took 2.09–3.68 times the CPU of the same meter with cache simulation disabled. These are local pinned-CPU costs. The experiment included five rounds per arm for seven inputs, verified actual stdout, matched the full cache fields between both simulators, and reconciled live and terminal work across all three arms. [Per-run measurements and exact counter comparisons](../results/cpp20-v8-memory/rep-movs-cost.json)

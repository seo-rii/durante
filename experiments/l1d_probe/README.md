# L1 data-cache probe

This Linux x86-64 experiment counts demand accesses to a cold 32 KiB virtual-address cache with 64-byte lines, 64 sets, eight ways, write allocation and exact LRU replacement. It produces aggregate counts alongside the usual Durante work report. The Aonohako judging clock uses its existing instruction and REP counters.

## Build and check

From the repository root, prepare the pinned SDK and use its patched runtime. The model test compares every tag's LRU position and all counters to an independent per-element implementation. The assembly control checks the DynamoRIO instrumentation against a hand-counted trace, including saved registers, flags, read-modify-write order and overlapping backward copies.

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

The optimized client combines cache accounting for an ordinary store with Durante's existing write checkpoint. Its scalar callbacks use only integer registers, so the runtime can omit SIMD saves for those calls. A read followed immediately by a write to the same operand shares one LRU update and counts both references. XLAT uses the low byte of RAX for its index, including with a 32-bit address override.

## Compare clients

Build the scalar reference from the revision immediately preceding the optimization. The reference performs a clean call per ordinary operand and simulates every REP element individually.

```sh
git worktree add --detach ../durante-l1d-reference 98bda4890994
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

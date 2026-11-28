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

## Larger capacities and dependent loads

`-probe_outer` (with `-probe_l1d`) also feeds every line that misses the L1D model, in
miss order, to fully associative LRU caches of 256 KiB, 2 MiB and 32 MiB
(`outer.h`). One LRU stack with a boundary node per capacity gives all three exactly.
A stderr line beginning with `PROBE_OUTER` reports, per capacity, the misses, the
stream misses (within two lines of one of the last 16 misses at that capacity) and the
misses of dependent loads: loads whose address register holds a value loaded earlier
in the same block, through register arithmetic or around a block that branches to its
own start. `l1d_dependent` counts the dependent loads' L1D misses. A four-line assembly trace checks that LEA propagates a dependency through a loaded
base or index and clears it when both are independent. The model test
checks the counts, the full LRU order and every boundary against a literal
stack-distance reference, also behind the L1D model and its bulk REP paths. See
[docs/CAPACITY_MISSES.md](../../docs/CAPACITY_MISSES.md).

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

For long REP MOVS, the probe checks whether the source and destination cover disjoint cache lines, start at the same offset within a line, are naturally aligned and stay within the effective address space. It handles forward and backward traversal. It simulates the first 256 line pairs in order. Four passes by each stream through the 64 sets replace all eight ways, so each subsequent pair adds two misses. The probe reconstructs the eight most recent tags directly for each set from the final read and write lines. A write wins when a read and write touch the same set in the same iteration. Short transfers and other layouts use the existing per-line or per-element simulation.

### Local CPU measurements

On a pinned AMD EPYC 7B12 core, each assembly fixture copied 2 GiB in disjoint chunks of one size. The table gives median child CPU time across seven rounds.

| Chunk size | Previous probe, ms | Updated probe, ms | Cache disabled, ms |
| --- | ---: | ---: | ---: |
| 32 KiB | 316.695 | 192.099 | 79.213 |
| 64 KiB | 307.064 | 130.769 | 74.433 |
| 256 KiB | 112.001 | 81.473 | 68.647 |

The six CPP20 fixtures had smaller changes. Across five rounds, median CPU fell by 1.2%, 6.8% and 2.5% on the three standard copy inputs and rose by 1.4% on the fill input. For the crossed controls it fell from 624.0 to 606.7 ms with a small working set and hot copies, and rose from 2,283.6 to 2,353.0 ms with the large working set and no explicit copies. The five readings for this last case ranged from 2,162 to 2,570 ms with the previous probe and from 2,227 to 2,448 ms with the updated probe.

Each of the 153 runs produced its expected stdout, matched its case's terminal work counters and published the same final live clock. Both enabled clients agreed on every reported cache field. The record retains individual CPU readings, arm order, binary identities and counts. [Paired results](../results/cpp20-crossed-memory/rep-movs-cost.json)

An earlier local experiment measured the optimization that first skipped guaranteed misses in the middle, with a 512-pair prefix and 512-pair tail. For its three CPP20 copy inputs the median CPU fell by 46.7%, 49.0% and 60.8% against the preceding implementation. Its optimized client is the baseline for the comparison above. [Earlier measurements](../results/cpp20-v8-memory/rep-movs-cost.json)

### Reproduce the comparison

Starting at the repository root, build the current probe as above. Use the previous implementation at commit `082b1ad` for the baseline, a patched 11.3 launcher, and an available logical CPU. The recorded run used CPU 5. These commands use Bash.

```bash
bench=$(mktemp -d)
cpu=5
git worktree add --detach "$bench/baseline" 082b1ad
cmake -S "$bench/baseline/experiments/l1d_probe" -B "$bench/build" \
  -DCMAKE_BUILD_TYPE=Release -DDynamoRIO_DIR="$sdk/cmake"
cmake --build "$bench/build" --target durante_l1d -j2
compare=(python3 -B experiments/l1d_probe/compare.py --drrun "$sdk/bin64/drrun"
         --baseline "$bench/build/libdurante_l1d.so" --candidate "$PWD/build/l1d/libdurante_l1d.so"
         --include-off --cpu "$cpu")
```

GNU ld retains the object basename as an ELF symbol. The names below reproduce the measured assembly binaries with GCC 12.2.0 and GNU binutils 2.40.

```bash
: > "$bench/in"
printf 'ZZZZZZZZ' > "$bench/out"
micro=()
while read -r bytes object; do
  cc -c -DCOPY_BYTES="$bytes" experiments/l1d_probe/stream_copy.S -o "$bench/$object"
  cc -nostdlib -static -no-pie -Wl,--build-id=none "$bench/$object" -o "$bench/movs-$bytes"
  micro+=(--case "movs-$bytes" "$bench/movs-$bytes" "$bench/in" "$bench/out")
done <<'FIXTURES'
32768 ccSZZ5MN.o
65536 ccfm0Ly3.o
262144 ccVUoj1b.o
FIXTURES
"${compare[@]}" --rounds 7 --output "$bench/micro" "${micro[@]}"
```

For the six CPP20 inputs, set `AONOHAKO` to a local Aonohako repository containing the commit in `experiments/load_corpus.json`. The existing preparation helper checks the historical sources and compiled ELF hashes using the compiler image in that manifest. Build the crossed control with the compiler and flags in its frozen manifest. [Crossed corpus](../crossed_memory_corpus/manifest.json)

```bash
PYTHONPATH=experiments python3 -B - "$AONOHAKO" "$bench/cpp" <<'PYTHON'
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from collect_load_model import CORPUS, prepare

aonohako, output = map(Path, sys.argv[1:])
corpus = json.loads(CORPUS.read_bytes())
corpus["families"] = {name: corpus["families"][name] for name in ("rep_copy", "rep_fill")}
prepare(SimpleNamespace(output=output, aonohako=aonohako), corpus)
for name, family in corpus["families"].items():
    for case in family["cases"]:
        stem = f"{name}-{case['scale']}"
        (output / (stem + ".in")).write_bytes(case["stdin"].encode())
        (output / (stem + ".out")).write_bytes(case["stdout"].encode())
PYTHON
cross=experiments/crossed_memory_corpus
g++ -std=c++20 -O2 -static -no-pie -march=x86-64 -mtune=generic \
    -Wl,--build-id=none -s "$cross/crossed/main.cpp" -o "$bench/crossed"
cpp=()
for id in rep_copy-1 rep_copy-2 rep_copy-4 rep_fill-2; do
  cpp+=(--case "$id" "$bench/cpp/${id%-*}/Main" "$bench/cpp/$id.in" "$bench/cpp/$id.out")
done
for id in cross-rep256-small cross-rep0-large; do
  cpp+=(--case "$id" "$bench/crossed" "$cross/$id/in" "$cross/$id/oracle")
done
"${compare[@]}" --rounds 5 --output "$bench/paired" "${cpp[@]}"
git worktree remove "$bench/baseline"
```

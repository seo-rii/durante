# Branches and load locality in the CPP20 search/window comparison

In the earlier experiment the largest binary search took longer than the smallest
rolling window, although it executed fewer instructions and fewer load instructions.
This replay examines two additional counters on the same six inputs. Native CPU
observations come from the prior study. [CPP20 load-model experiment](CPP20_LOAD_MODEL.md)

| Algorithm | Scale | Native median (ms) | Simulated branch mispredictions | Nonadjacent scalar MOV loads |
|---|---:|---:|---:|---:|
| Binary search | 1 | 33.8320 | 2,103,690 | 529 |
| Binary search | 2 | 55.7055 | 4,205,373 | 529 |
| Binary search | 4 | 108.2435 | 8,408,315 | 529 |
| Rolling window | 1 | 22.7450 | 1,059 | 507 |
| Rolling window | 2 | 40.5125 | 1,059 | 507 |
| Rolling window | 4 | 77.9335 | 1,059 | 507 |

All three repetitions agreed in every cell. To place both estimates within
±20% of their reference times the predicted search/window ratio must reach
at least two thirds of the native ratio. Using the medians for search 4 and
window 1 gives 216487/68235 (3.173). Even using the fastest search (102.772 ms)
and the slowest window (29.048 ms) gives 25693/10893 (2.359). Gshare misses
spanned both lower bounds with a ratio of 7,939.86. Total conditional branches,
instructions, REP bytes, the production latency bound, all load instructions,
and each scalar MOV locality category fell short. For matching scales the
median-based lower bounds are all below one, so those pairs do not impose the
same ordering requirement. The receipt includes both comparisons and the ranges
for every counter. [Replay receipt](../experiments/results/cpp20-pattern-replay.json)

The counters model different events. The 14-bit gshare simulation uses static
branch addresses and the preceding conditional branch outcomes. Its misses are
software predictions. The locality probe classifies the starting 64-byte line
of each eligible integer MOV-family load relative to that instruction's previous
line. It excludes addresses using RSP/RBP, memory operands folded into arithmetic
or compares, and vector loads. Disassembling the frozen executables explains
the nearly constant locality counts. The search reads its table in a memory
`cmp` at 0x4016cf. The window reads two values per iteration with memory `add`
and `sub` at 0x4016cf and 0x4016d3. Their hot reads are outside this counter's
coverage. These results identify a candidate branch feature on previously seen
algorithms. Its cost in nanoseconds and its prediction error on other
algorithms remain open. [Counter definitions and crossed controls](REFERENCE_FEATURES.md)

## Reproduce

Use the compiler image and verified fixture preparation from the earlier study.
Set `FIXTURES` to the result of `collect_load_model.py prepare`, `SDK` to the
pinned DynamoRIO 11.3 development SDK, `BUNDLE` to the original Aonohako
judge bundle, and `CPU` to an available logical CPU. The replay checks the
launcher's, core's, drmgr's and production meter's hashes against the published
collection. The exploratory client only needs the bundle's existing runtime
libraries. The library obtains address registers with a clean-call machine
context so it can run alongside the judge's CPUID emulation. [Fixture preparation](CPP20_LOAD_MODEL.md#reproduce)

```sh
RUN=$(mktemp -d)
cmake -S experiments/time_features -B "$RUN/build" -DCMAKE_BUILD_TYPE=Release \
    -DDynamoRIO_DIR="$SDK/cmake"
cmake --build "$RUN/build" --target time_features_client
cc -nostdlib -static -no-pie -Wl,--build-id=none experiments/time_features/control.S -o "$RUN/control"
python3 experiments/cpp20_pattern_replay.py --bundle "$BUNDLE" --fixtures "$FIXTURES" \
    --probe "$RUN/build/libtime_features_client.so" --control "$RUN/control" \
    --cpu "$CPU" --output "$RUN/results"
```

Each program uses its original static ELF, stdin and stdout oracle. Every
measured execution runs the patched judge core, production client with
`x86-64-v2` and experimental client together. The runner requires identical
same-run instruction counts, one thread/process/image, uninterrupted exit,
complete REP coverage and the production diagnostic integrity contract.
The four crossed controls also check the expected cache-line counts and
gshare directions. Linux ASLR was enabled globally (2) and disabled for each
replay by `setarch -R` (personality 0x40000). The reference timings are
unmodified #53 host-native direct-child CPU observations. This metadata does
not equate the native CPU's glibc dispatch or hardware predictor with the
instrumented runtime. Raw process records and feature JSON are retained under
the output directory. No replay duration is used as a target time.

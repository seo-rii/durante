# Timing two loads in one cache line

One static Linux x86-64 executable performs 96 million adjacent eight-byte stores and loads. Input `0` loads the bytes just stored. Input `1` shifts the load by one byte, so seven of its eight bytes overlap the store. Both ranges fit in the same aligned cache line. Both modes check the final value of their respective recurrence and print `ok`. They follow the same instruction and branch sequence. [Assembly control](control.S)

| Input | V8 live instructions | REP MOVS / STOS bytes | Native CPU range | Native median | Native at 406 ms | Historical weights on v8 at 406 ms |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| Covered | 480,000,027 | 0 / 0 | 200.774–212.861 ms | 210.588 ms | 3 AC | AC |
| Partial overlap | 480,000,027 | 0 / 0 | 589.807–649.848 ms | 601.570 ms | 3 TLE | AC |

These are actual Aonohako HTTP results on one Seoul Cloud Run Gen2 instance with one vCPU. Each of three live runs per mode had an uninstrumented run on either side. The two native medians give the common 406 ms test limit when their midpoint is rounded to the nearest millisecond. The historical three-counter model selected the same verdict for both modes. [Cloud counters, native brackets and boundary verdicts](../results/store-load-v8-clock.json)

Any estimator using only these three live counters must give both inputs the same prediction. For target times (A < B), the least possible worst relative error of a common prediction is `(B − A) / (B + A)`. The measured medians give a lower bound of 48.14%. Even choosing the closest observed endpoints, 212.861 ms and 589.807 ms, gives 46.96%. Both inputs separately passed the 20 ms duration and 10% native spread gates.

A fixed-address local replay also matched on all 19 counters in the published CPP20 feature screen, including 96,000,001 load instructions, 17 simulated branch misses and a static latency bound of 1,920,000,108 sixteenths of a cycle. The 32 KiB L1D simulation counted 192,000,001 line accesses and two misses in each mode, with zero skipped operands. The byte-range probe distinguished all 96 million pairs. The overlap can affect store-to-load forwarding on a processor. The experiment measures the address relationship and CPU time, without measuring a hardware stall event. [Feature definitions](../joint_latency_screen.py) · [L1D model](../l1d_probe/README.md)

## Byte-range probe

The optional DynamoRIO client counts a GPR-to-memory MOV immediately followed by a memory-to-GPR MOV in the same original basic block. It handles 1-, 2-, 4- and 8-byte operands with ordinary 64-bit base/index/displacement addressing. At the load it classifies the requested bytes as fully covered by the store, partially overlapping, or disjoint. A clean call runs only at an eligible pair. Use a trusted uninterrupted single-thread, single-image program and the patched runtime with traces disabled. Counts describe attempted loads. Stores and loads separated by another instruction or a block boundary, SIMD accesses and other addressing forms fall outside the experiment.

On the fifteen calibration cases and three independent CPP20 holdouts, the probe saw zero partial-overlap pairs. This feature diagnoses the controlled timing gap. The general-purpose programs still need other information to predict their native times. [Case counts](../results/store-load-v8-clock.json) · [Blind timing study](../../docs/CPP20_V8_CLOCK.md)

## Reproduce

Set `SDK` to the pinned patched DynamoRIO SDK directory and `METER` to a v8 production `libdurante.so`. The commands below run from the repository root on Linux x86-64. Choose an available logical CPU in `CPU`. The comparison uses Python 3.11 or newer and supports a host that permits per-process ASLR disabling.

```sh
RUN=$(mktemp -d)
DRRUN="$SDK/bin64/drrun"
cmake -S experiments/store_load -B "$RUN/pair" -DCMAKE_BUILD_TYPE=Release \
  -DDynamoRIO_DIR="$SDK/cmake" -DDURANTE_TEST_DRRUN="$DRRUN"
cmake --build "$RUN/pair"
ctest --test-dir "$RUN/pair" --output-on-failure
cmake -S experiments/time_features -B "$RUN/features" -DCMAKE_BUILD_TYPE=Release -DDynamoRIO_DIR="$SDK/cmake"
cmake --build "$RUN/features" --target time_features_client
cmake -S experiments/l1d_probe -B "$RUN/cache" -DCMAKE_BUILD_TYPE=Release -DDynamoRIO_DIR="$SDK/cmake"
cmake --build "$RUN/cache" --target durante_l1d
cc -nostdlib -static -no-pie -Wl,--build-id=none experiments/store_load/control.S -o "$RUN/control"
python3 -B experiments/store_load/compare.py --program "$RUN/control" --cpu "$CPU" \
  --drrun "$DRRUN" --meter "$METER" --time-features "$RUN/features/libtime_features_client.so" \
  --l1d "$RUN/cache/libdurante_l1d.so" --store-load "$RUN/pair/libstore_load_probe.so" --output "$RUN/results"
```

`summary.json` contains the binary hashes, three pairs of native CPU samples per input, the 19 counters, the L1D totals and the byte-range counts. Native timing varies across hosts. The collector checks numerical output, repeatability of the counters, equality across the two inputs, and the expected overlap class for each input.

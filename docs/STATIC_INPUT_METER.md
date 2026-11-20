# Cost of live accounting after static programs read input

On v8 the histogram stack benchmark took 2.76 s of CPU on a Cloud Run worker with live accounting and 46.45 ms natively. A local control isolated most of that cost to the ordinary meter. Eligible static basic blocks first translated during startup used compact instrumentation for the lifetime of their cached translation. The histogram program enters a multi-block loop shortly after parsing stdin. [Original worker and local controls](CPP20_CROSSED_MEMORY.md#cost-of-the-ordinary-live-meter)

For the ordinary live clock, a thread that received a positive byte count from a read or readv on descriptor zero can now use inline instrumentation for eligible newly translated static blocks after 16,384 charged instructions. Other blocks keep their existing selection rules, including the 131,072-instruction threshold and the self-loop fast path. Stored translations and the existing static executable text checks apply. This affects the CPU cost of collecting work. The time budget still uses instructions and typed REP bytes.

## Paired measurements

The local benchmark ran five alternating pairs on a pinned AMD EPYC 7B12. Both arms used the same launcher, actual program path, inputs and 32 MiB runtime settings. For all 110 runs the candidate reproduced expected stdout, all terminal work fields and the shared live instruction, MOVS and STOS counters. The histogram median fell by 86.3%. The short iostream program took 13.9 ms more. Small percentage changes on the other programs varied across the five pairs. [CPU samples, run order and workload identities](../experiments/results/cpp20-static-input/local.json)

| Local program | Previous median CPU | Updated median CPU |
|---|---:|---:|
| Histogram stack | 1,960.65 ms | 268.31 ms |
| Count distinct subarrays | 130.77 ms | 137.13 ms |
| Sparse-table range minimum | 183.40 ms | 187.32 ms |
| Short C++ iostream read and print | 94.86 ms | 108.73 ms |
| Short scanf and printf | 60.47 ms | 59.73 ms |
| Eight-instruction assembly startup | 10.76 ms | 10.82 ms |

The complete local record includes the integer, branch, copy and fill controls, including a one-iteration integer read. The three short diagnostic fixture sources and their inputs and outputs are embedded in their individual records. To reproduce a pair for a given executable, use the two Release clients built against the same DynamoRIO 11.3 development headers and the same patched launcher. The historical client source is the parent of commit `2795424`. [Measurement runner](../experiments/l1d_probe/compare.py)

```sh
python3 -B experiments/l1d_probe/compare.py --ordinary-live --rounds 5 --cpu "$cpu" \
  --drrun "$sdk/bin64/drrun" --baseline "$old_client" --candidate "$new_client" \
  --output "$new_results_directory" --case histogram-stack "$program" "$input" "$expected_stdout"
```

## Direct access to the static live accounting state

The client at `191858a` retains the input and instruction thresholds above. When charging a block or saving a write checkpoint in generated code, it reads the accounting pointer directly from one DynamoRIO raw TLS slot. Each thread initializes the slot from its own accounting state, and a forked child restores its surviving thread's pointer. If the slot cannot be reserved, the generated code uses the existing lookup. Only the ordinary static live clock requests the slot.

We compared the v9 client and this change in 16 alternating rounds on logical CPU 5 of an AMD EPYC 7B12 VM. The two arms in each round ran the same file from the same path. Each consecutive pair of opposite-order rounds contributed one block. The paired change is the median of the eight block log ratios converted back to a percentage. Marginal CPU medians and the paired estimate can therefore have different signs. CPU time covers the measured process and its instrumentation. [Frozen local protocol](../experiments/results/cpp20-static-tls/protocol.txt) · [All 448 executions, timings, orders, binary digests and work counters](../experiments/results/cpp20-static-tls/local.json)

| Program | v9 median CPU | Direct TLS median CPU | Paired change | Faster blocks |
|---|---:|---:|---:|---:|
| Short C++ iostream | 175.21 ms | 169.40 ms | −3.24% | 6/8 |
| New sorted intersection, empty | 183.40 ms | 173.02 ms | −3.49% | 6/8 |
| New sorted intersection, 256 items per list | 180.93 ms | 173.69 ms | −3.05% | 5/8 |
| New sorted intersection, 262,144 items per list | 195.36 ms | 182.03 ms | −4.38% | 8/8 |
| Monotonic-stack histogram | 341.23 ms | 307.10 ms | −12.80% | 7/8 |
| Sparse-table range minimum | 257.57 ms | 241.02 ms | −5.70% | 8/8 |
| Integer control | 386.38 ms | 339.31 ms | −12.99% | 8/8 |
| Branch control | 1,032.34 ms | 902.88 ms | −12.73% | 8/8 |
| Short scanf | 93.29 ms | 93.33 ms | +0.81% | 4/8 |
| Short REP | 96.27 ms | 95.20 ms | +0.80% | 4/8 |

The host was shared. In a separate 96-execution control, two copies of v9 produced apparent paired changes of −4.85% for short iostream, +9.98% for short scanf and +5.00% for short REP. Small short-program changes on this machine do not establish a startup speedup. The integer, branch and histogram programs showed larger gains. All runs matched expected stdout, terminal work and the shared instruction, MOVS and STOS counters. These tests concern collection cost. The work-to-time coefficients and limits were held fixed. [Identical-client results](../experiments/results/cpp20-static-tls/identical.json)

The three sorted-intersection cases share one static executable. Source, executable, input and expected output digests were fixed before evaluating the chosen client on that program. The output oracle summed the set intersection of two independently generated increasing lists. For the recorded executable, build with Debian g++ 12.2.0 (12.2.0-14+deb12u1) and the flags below. Use the paired runner above with `--rounds 16`, then run the summarizer on its output. [Source and fixed inputs](../experiments/static_input_transfer) · [Digest manifest](../experiments/static_input_transfer/manifest.json)

```sh
g++ -std=c++20 -O2 -static -no-pie -march=x86-64 -mtune=generic -Wl,--build-id=none -s \
  experiments/static_input_transfer/main.cpp -o Main
python3 -B experiments/summarize_live_pairs.py "$new_results_directory/summary.json"
```

# Sampling consecutive load addresses on a pinned Compute Engine CPU

**The sampler distinguished adjacent and scattered traversal in all 12 control runs. Charging for that difference did not improve the Rust pass count.** The frozen clock met the ±20% error target on five of eight Rust programs. The best candidate on the synthetic controls also passed those same five Rust cases. In particular, it estimated 120.89 ms for a binary search with a native median of 34.45 ms. Sampling added 0.68% to total instrumented CPU across 36 matched comparisons. The sampler remains a research client. [Measured data](../experiments/results/gce-access-sample/dataset.json.gz) · [Evaluation](../experiments/results/gce-access-sample/evaluation.json)

## Accuracy on controls and Rust programs

The two adjacent programs produced zero far pairs. Both scattered programs produced 67,584 far pairs out of 67,584 per run. An independent calculation from the original successor table matched every classification count at all four load instructions in all three rounds. The two scattered programs nevertheless consumed very different amounts of native CPU because one traversed a single dependency chain and the other interleaved four. [Address-order verification](../experiments/access_sample/check_locality.py) · [Original control design](GCE_DEPENDENCY_PAIRS.md)

| Workload | Native median | Frozen clock | Far penalty | Scaled far penalty |
| --- | ---: | ---: | ---: | ---: |
| Adjacent, one chain | 116.22 ms | 114.71 ms | 114.71 ms | 114.71 ms |
| Adjacent, four chains | 35.69 ms | 31.81 ms | 31.81 ms | 31.81 ms |
| Scattered, one chain | 1,258.19 ms | 114.71 ms | 414.16 ms | 1,122.21 ms |
| Scattered, four chains | 287.26 ms | 31.81 ms | 331.26 ms | 312.69 ms |
| Rust sort | 40.36 ms | 36.73 ms | 48.98 ms | 47.54 ms |
| Rust GCD | 39.35 ms | 62.02 ms | 62.02 ms | 62.02 ms |
| Rust binary search | 34.45 ms | 21.66 ms | 51.15 ms | 120.89 ms |
| Rust rolling window | 16.38 ms | 20.07 ms | 20.07 ms | 20.07 ms |

The table uses the median of the three estimates per program. The frozen clock passed 2/4 assembly controls and 5/8 Rust programs. The simple far penalty passed 3/4 and 4/8. The scaled penalty passed 4/4 and 5/8. The latter coefficient was 14.56 ns per scaled far load. Even within the assembly corpus the fit was sensitive to its training set. Training it only on the two serial programs overestimated the scattered four-chain case by 22.05%. Training on the two four-chain programs underestimated the scattered serial case by 18.06%. Integer, copy, the branch workload and fill passed under all three models. Native fill spread was 11.89%, above the 10% gate. Rolling window was below the 20 ms duration floor. Both are included. [All cases, estimates and ranges](../experiments/results/gce-access-sample/evaluation.json)

The affected workloads cannot both meet ±20% with one nonnegative far-load penalty and the frozen baseline. Even allowing the most favorable native endpoints and diagnostic readings, serial scattered traversal requires **more than 12.60 ns per far load**, while binary search permits **at most 3.17 ns**. Both programs saturate the scaling factor at one, so the same bound applies to both candidate features. Correcting the binary-search sampling bias below would increase its estimated far-load count and make its upper bound tighter. [Coefficient bound from the archived measurements](../experiments/access_sample/check_penalty.py)

GCD's main division loop used register operands. The entire executable recorded only 221 eligible scalar loads per run. Adding a nonnegative memory penalty leaves its +57.63% error essentially untouched. The division dependency and throughput controls from the preceding study remain relevant to this workload. [Previous divide controls](GCE_DEPENDENCY_PAIRS.md)

## Additional measurement cost

| Paired instrumented runs | CPU time without / with sampler | Increase in total CPU | Median paired increase |
| --- | ---: | ---: | ---: |
| All 36 | 114.700 s / 115.480 s | 0.68% | 0.91% |
| 24 Rust pairs | 102.226 s / 102.462 s | 0.23% | 0.66% |
| 12 assembly pairs | 12.473 s / 13.018 s | 4.37% | 2.92% |

The largest per-program increase in total instrumented CPU was 5.58% for scattered serial traversal. With baseline first or sampler first, the corresponding overall increases were 0.67% and 0.69%. Each program has just three pairs on one CPU. The assembly controls returned bit-identical baseline and sampled production counters. Across the Rust cases the maximum differences in paired totals were 216 ppm for instructions, 71 ppm for modeled cycles, and 3.0035% for nominal operand bytes. For workloads with little counted memory traffic, the additional startup work can matter relative to that counter. The CPU percentages compare two already instrumented modes and include loading the second shared library. [Per-program cost and counter differences](../experiments/results/gce-access-sample/evaluation.json)

## What the sampler observes

The research client tracks selected scalar integer reads in the main executable. At each machine instruction it compares the effective addresses of visits 1 and 2, then 1025 and 1026, and so on. It labels a pair **near** if the two starting addresses occupy the same or adjacent 64-byte lines and **far** otherwise. The up-front pair also gives short-running instructions a chance to appear in the sample. The report stores the instruction's ELF offset and counts. [Sampler source](../experiments/access_sample/probe.c)

The ordinary path decrements a per-instruction countdown inline. When the application will overwrite the affected flags, this takes a decrement and conditional jump. The other path preserves the flags and RCX. Only the two sampled visits per period call the address observer. The experiment uses a single-thread, single-image, uninterrupted process and disables DynamoRIO traces. It excludes library code, SIMD loads, read-modify-write operations and implicit memory accesses. A fixed phase can repeatedly sample the same part of a periodic access pattern. The distance between successive visits to one instruction gives no direct measurement of cache misses, reuse across other instructions, memory-level parallelism or DRAM latency. [Frozen protocol](../experiments/access_sample/protocol.json)

## What the comparison measures

We reused the exact four assembly executables from the matched 16 MiB successor-table study and eight optimized Rust executables with their original scale-one inputs and output oracles. Each of three rotated rounds bracketed a baseline and a sampled diagnostic with two native executions. The order of the diagnostics alternated by case and round. Both used the same production client, x86-64-v2 CPUID profile, REP counters, latency bound and memory operand bytes. The sampled mode also loaded the research client. All reported times are child user plus system CPU time from `wait4`. Native time is the prediction target. [Executable and input provenance](../experiments/access_sample/fixtures.py) · [Collector](../experiments/access_sample/collect.py)

The baseline uses the two weights from the earlier Rust study. For each sampled instruction we multiplied its dynamic load count by its observed fraction of far pairs, then summed those estimates to give `F`. Sites with fewer than two visits contribute zero. We tested two additions to the baseline, each with one nonnegative fitted coefficient. The first charges for `F` directly. The second uses `F × min(1, modeled cycles / (5 × eligible loads))`. This proxy gives less weight to loads when the modeled cycles per load are low. Other computation in the same program also affects it. Both coefficients were fitted only on the four assembly controls. All eight Rust programs were held out from those fits. The earlier baseline calibration had used integer and copy. Each case must put all three predictions within ±20% of its six-native median to pass. [Frozen scoring code](../experiments/access_sample/score.py) · [Prior clock calibration](GCE_RUST_OPERAND_BYTES.md)

## Why the sample can miss a search pattern

The original Rust binary performs 18 indexed comparisons per query at ELF offset `0x1535a`, then one final indexed comparison at `0x15302`. The 1,024-visit cadence only starts inner-loop pairs at even-numbered depths (counting from zero). Across all 9,437,183 consecutive transitions at the inner comparison, **72.221%** were far for every possible array alignment when rounded to three decimals. Sampling reported **66.667%** (6,144 of 9,216). The final comparison reached 100% in the sample and about 99.98% in the full sequence. The reconstruction reproduced the original executable's checksum over 524,288 queries. Changing the period to 1,021 in the offline replay gave 72.220% for the inner comparison. That period was chosen after seeing the loop and has no measured CPU cost or timing score here. [Exact ELF and input replay](../experiments/access_sample/check_search_phase.py)

A small periodic program makes the limitation visible at a single instruction. Of its 2,049 consecutive transitions, 2,044 were far. All three sampled pairs were near. A separate hand-counted program exercised both counter paths with live flags and RCX. It yielded the expected 4,101 loads and six pairs split evenly between near and far. [Periodic fixture](../experiments/access_sample/phase_control.S) · [Hand checks](../experiments/access_sample/check_control.py)

## CPU placement and teardown

A temporary private Seoul N2D VM exposed two AMD EPYC 7B13 cores with one thread per core. All 72 native children and 72 instrumented children ran on CPU 1. The scheduler trace also captured all 72 same-PID DynamoRIO loader executions there. The observer on CPU 0 polled 969 times. The trace reported zero dropped events and CPU 1 accumulated zero guest steal ticks during the 243-second measurement. Independent release builds reproduced both client binaries byte for byte. After export, lookups of both the instance and its boot disk returned HTTP 404, and the temporary SSH key was removed. [Platform and cleanup evidence](../experiments/results/gce-access-sample/platform.json) · [Placement trace summary](../experiments/results/gce-access-sample/placement.json.gz)

## Replaying the measurements

Run from the repository root. The original workload archives already contain the executables. The receipt verifier checks their hashes as well as the inputs, outputs and both instrumentation records.

```sh
study=experiments/access_sample
data=experiments/results/gce-access-sample
python3 "$study/score.py" "$data/dataset.json.gz" --verify "$data/evaluation.json"
python3 "$study/check_penalty.py" "$data/dataset.json.gz"
python3 "$study/check_locality.py" "$data/dataset.json.gz"
python3 "$study/check_search_phase.py" --dataset "$data/dataset.json.gz"
python3 "$study/verify_receipts.py" "$data/dataset.json.gz" "$data/receipts.tar.xz"
python3 "$study/verify_placement.py" "$data/dataset.json.gz" "$data/placement.json.gz" --platform "$data/platform.json"
```

To build the opt-in client on Linux x86-64, set `DynamoRIO_DIR` to the `cmake` directory of the DynamoRIO 11.3.0-1 SDK. The measured client used GCC 12.2.0, a Release build and the same patched x86-64 runtime as the preceding study. Both the reader and the binary digests are pinned in the protocol. The collector takes absolute paths to the runtime bundle, the normal Durante client, the research client and the archived executable directory. [Build target](../experiments/access_sample/CMakeLists.txt) · [Collector arguments and launch order](../experiments/access_sample/collect.py)

```sh
cmake -S experiments/access_sample -B /tmp/access-sample-build -DCMAKE_BUILD_TYPE=Release -DDynamoRIO_DIR="$DynamoRIO_DIR"
cmake --build /tmp/access-sample-build --target access_sample
```

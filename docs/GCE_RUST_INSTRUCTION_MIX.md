# Rust instruction mix on a pinned Compute Engine vCPU

**The recurrence-only probe took 23.39% less diagnostic CPU than the full probe on three new Rust programs.** Their predicted times differed by at most 0.0171%, but the frozen clock met the ±20% native-time target on only one of the three. Binary search was underestimated by 42.04%. [Prospective measurements](../experiments/results/gce-rust-instruction-mix/holdout/evaluation.json)

This study compares instruction count, scalar integer multiply count and loop-carried register dependencies as predictors of uninstrumented Rust process CPU time. An earlier Aonohako experiment found that integer/4 took at least 4.20 times as much native CPU as sort/1, despite their ordinary Durante counters permitting at most a 1.265-fold difference. The new study measures all timers and counters again on the same fresh executables. [Earlier GCE measurement](https://github.com/seo-rii/aonohako/blob/f04ae258094e099c524c9191ef2f7b8a4c4f772a/docs/durante/GCE_PINNED_RUST_V10.md) · [Prespecified protocol](../experiments/rust_instruction_mix/protocol.json)

## Results

| Features fitted on integer and copy | Validation cases within ±20% | Stable validation cases within ±20% | Median case error¹ | Largest signed error |
| --- | ---: | ---: | ---: | ---: |
| Instructions | 0/9 | 0/8 | 50.75% | +93.51% on branch/4 |
| Instructions and scalar multiplies | 4/9 | 3/8 | 24.92% | +157.59% on branch/4 |
| Instructions and loop recurrence | 3/9 | 3/8 | 25.05% | +28.41% on branch/4 |

¹ For each case take the largest absolute error of its three predictions. The table gives the median of these nine values. The recurrence fit passed all three sort cases. It overestimated branch by 25.61–28.41% and fill by 20.53–26.93%. The scalar multiply fit passed the three fill cases and sort/1. [All predictions and exact fitted coefficients](../experiments/results/gce-rust-instruction-mix/evaluation.json)

**The extra counters distinguish the original integer and sort examples.** Native CPU medians were 168.415 ms on integer/4 and 40.228 ms on sort/1. Even allowing the most favorable native and counter endpoints, instruction count alone forces at least 51.92% error on one of them. Across three repeats the integer case executed 134,223,331 scalar multiplies and sort executed 5,588. Those counts eliminate the positive lower bound for this pair when scalar multiplies are allowed their own weight. [Counter ranges and pair calculation](../experiments/results/gce-rust-instruction-mix/evaluation.json)

The integer ELF carries the result of one scalar multiply and add into the next loop iteration. The compiled loop at ELF virtual address `0x14f10` advances eight source iterations at a time. In comparison, branch/4 executed 268,436,948 scalar multiplies. Each source iteration calculates its term from the loop index. The recurrence probe counted 8,589,947,552 units on integer/4 and 13,120 on branch/4. A single price per scalar multiply overestimated the branch case by 157.59%. The sort ELF also contains packed integer multiply instructions (`PMULUDQ`) outside the scalar multiply count. Their contribution to native CPU time was not measured separately. [Five exact ELFs and raw records](../experiments/results/gce-rust-instruction-mix/samples.tar.xz)

**Native controls and cost.** All 90 native executions exceeded 20 ms (40.036–235.713 ms). The median within-case spread across 15 cases was 5.14%. Thirteen cases were within 10%. Copy/2 (10.68%) and fill/2 (12.23%) exceeded it. The 45 dual-client diagnostics used 800.034 CPU seconds, compared with 4.984 seconds for the summed per-triplet means of the native brackets (160.51×). That ratio includes the ordinary meter, full research probe and DynamoRIO. This run did not time either client alone. [Raw triplets](../experiments/results/gce-rust-instruction-mix/dataset.json.gz)

On one Seoul N2D VM, the scheduler trace matched all 135 wait4 PIDs, including 45 same-PID diagnostic loader re-execs on guest CPU 1. All 5,116 trace records reconciled with the per-CPU buffers. There were no recorded drops or overruns. The independent observer made 3,234 polls from CPU 0 and saw 101 distinct container TIDs, each restricted to CPU 1. Guest CPU 1 accumulated zero steal ticks during the 811-second collection. These observations concern guest placement on one AMD EPYC 7B13. This experiment uses a standalone diagnostic process, and all five freshly compiled ELF hashes differ from the earlier Aonohako study. [Placement receipt](../experiments/results/gce-rust-instruction-mix/placement.json.gz) · [VM configuration and cleanup](../experiments/results/gce-rust-instruction-mix/platform.json)

### Retrospective coefficient check

After scoring the three prespecified models, we asked whether any choice of the two recurrence weights could explain the observed times. The coefficients `(80 × instructions + 13 × loop_recurrence_cycles_x16) / 1000` in nanoseconds place all 45 observations within ±20% of their native case median. The largest absolute error is 15.10% on copy/1. The choice used all 15 cases, including their validation outcomes. The prespecified recurrence fit's validation score remains 3/9. These retrospective coefficients are frozen for a separate test on previously unmeasured programs.

## Measurement and scoring

Each of five optimized Rust programs runs at three scales. Each case has three native → diagnostic → native triplets, with the starting case rotated between rounds. All phases read the same input and must produce its published expected stdout. Each native control uses the final user plus system CPU time of its own child from `wait4`. The diagnostic runs the ordinary v10 meter with its x86-64-v2 CPUID profile and the existing research feature client in the same process. The collector requires both clients to agree on instructions. Native code sees the host CPUID, so a library can select a different instruction path on the two executions of an identical ELF. The probe counts loads, stores, three classes of control flow, scalar integer multiplies and divides, and floating-point instructions at basic-block entry. Classes can overlap. The multiply count covers x86 `IMUL`, `MUL` and `MULX`. The recurrence counter estimates register/flag dependency latency in basic blocks that branch back to their own entry. [Probe source](../experiments/time_features/featclient.c) · [Collection script](../experiments/rust_instruction_mix/collect.py)

The protocol fixes three models before collecting data. All use a nonnegative instruction weight and zero intercept. The second can also weight scalar integer multiplies, and the third can weight the loop recurrence estimate. Each model minimizes squared relative error on integer and copy, using one native median per scale. Branch, sort and fill supply nine validation cases. A case passes when all three predictions are within ±20% of the median of its six native controls. Native stability also requires every control to reach 20 ms and the range divided by the median to be at most 10%. The corpus and its previous timings were already known when choosing these features. [Scorer](../experiments/rust_instruction_mix/score.py)

## Prospective test on three new programs

The supplementary protocol fixed the retrospective coefficients and three new source/input/oracle pairs before measuring them on a second pinned VM. Euclidean GCD computes the GCDs of 524,288 pseudorandom pairs with the Euclidean remainder algorithm, lower bound makes 524,288 searches over 262,144 sorted keys, and rolling window sums overlapping windows over an array of 1,048,576 values for 16 sweeps. Independent Python oracles compute the expected checksums using the standard library GCD, an arithmetic lower bound, and a window-membership identity. [Frozen protocol and fixtures](../experiments/rust_instruction_mix/holdout/protocol.json) · [Oracles](../experiments/rust_instruction_mix/holdout/oracles.py)

Each of the three rounds runs both the full research client and its existing recurrence-only mode between two native controls. Both modes use the same production instruction meter. The lighter mode charges only eligible loop blocks for recurrence and omits the other research counters. Both mode orders occur for each program. We score the fixed nanosecond prediction against the six-native median, and examine whether each pair's two predictions agree within 0.1% of the full prediction. [Holdout collector](../experiments/rust_instruction_mix/holdout/collect_holdout.py)

| New Rust algorithm | Native CPU median | Worst fast prediction error | Paired median fast/full diagnostic CPU |
| --- | ---: | ---: | ---: |
| Euclidean GCD | 39.082 ms | +15.85% | 0.897 |
| Binary lower bound | 34.968 ms | −42.04% | 0.871 |
| Rolling window | 15.891 ms | +20.57% | 0.692 |

**The frozen clock passed one of three new programs in both modes.** GCD and binary lower bound both met the native duration and spread criteria. Lower bound took 33.611–36.309 ms across its six controls, while the recurrence-only prediction was about 20.27 ms. Rolling window remained below the 20 ms duration floor in every native run. [All prospective errors and paired timings](../experiments/results/gce-rust-instruction-mix/holdout/evaluation.json)

The lighter probe reduced total child CPU across nine matched pairs from 11.056 to 8.469 seconds (23.39%). The largest difference between paired clock predictions was 0.0171%, within the prespecified 0.1% in all nine pairs. The recurrence-only diagnostics still used 31.29 times the summed per-round mean native CPU on these three programs. Binary lower bound provides a repeatable case to investigate the cost of a search where each comparison selects the address of the next load. [Raw prospective dataset](../experiments/results/gce-rust-instruction-mix/holdout/dataset.json.gz)

The second guest had the same machine type, CPU model and image digest. The scheduler trace confirmed all 36 measured children and 18 same-PID loader re-execs on CPU 1. The VM and boot disk each returned HTTP 404 after deletion in both studies. [Prospective placement](../experiments/results/gce-rust-instruction-mix/holdout/placement.json.gz) · [Prospective platform and cleanup](../experiments/results/gce-rust-instruction-mix/holdout/platform.json)

## Reproduction

Replay the archived observations with Python 3.10 or later and the source revision used for collection.

```sh
experiment=experiments/rust_instruction_mix
results=experiments/results/gce-rust-instruction-mix
python3 "$experiment/verify_archive.py" "$results/dataset.json.gz" "$results/samples.tar.xz" --probe-build "$results/probe-build.json"
python3 "$experiment/placement.py" "$results/dataset.json.gz" "$results/placement.json.gz" --platform "$results/platform.json"
python3 "$experiment/score.py" "$results/dataset.json.gz" --verify "$results/evaluation.json"

holdout="$results/holdout"
python3 "$experiment/verify_archive.py" "$holdout/dataset.json.gz" "$holdout/samples.tar.xz" --probe-build "$results/probe-build.json"
python3 "$experiment/placement.py" "$holdout/dataset.json.gz" "$holdout/placement.json.gz" --platform "$holdout/platform.json"
python3 "$experiment/holdout/score_holdout.py" "$holdout/dataset.json.gz" --verify "$holdout/evaluation.json"
```

For a new collection, build the research client against the pinned DynamoRIO 11.3 SDK. Stage the two experiment JSON files and collector in `/study`, the root production `collect.py` in `/study/production`, and the five Rust sources from the pinned Aonohako revision in `/study/src`. These hashes identify the probe build, fixtures, image and ordinary client. [Probe build](../experiments/results/gce-rust-instruction-mix/probe-build.json) · [Inputs and output oracles](../experiments/rust_instruction_mix/corpus.json) · [Rust sources](https://github.com/seo-rii/aonohako/tree/f04ae258094e099c524c9191ef2f7b8a4c4f772a/internal/durante/reference/fixtures/runtime)

```sh
cmake -S experiments/time_features -B build-time-features -DCMAKE_BUILD_TYPE=Release \
  -DDynamoRIO_DIR="$PWD/toolkit/sdk/DynamoRIO-Linux-11.3.0-1/cmake"
cmake --build build-time-features --target time_features_client
```

Use the pinned Rust image, mount `/study` read-only with the built DSO, and mount a fresh output directory at `/out`. The single-thread-per-core VM exposes two guest CPUs. The benchmark container uses the host PID namespace, no network and a hard cpuset of 1. Housekeeping and the trace observer use CPU 0. In the container run the following command.

```sh
taskset -c 1 python3 /study/collect.py collect --corpus /study/corpus.json \
  --client /study/libtime_features_client.so --bundle /opt/durante --out /out --cpu 1
```

For a placement receipt start the pinned Aonohako trace script on CPU 0 before the collector, and stop it after the collector exits. Run `placement.py dataset.json placement.json.gz --capture TRACE_DIRECTORY` on the unmodified capture. The public receipt includes the observed fork, exec and exit records for all measured child PIDs. Run `verify_archive.py dataset.json samples.tar.xz --pack COLLECTOR_OUTPUT` to retain the five measured ELFs and their individual receipts. [Guest observer and usage](https://github.com/seo-rii/aonohako/blob/f04ae258094e099c524c9191ef2f7b8a4c4f772a/docs/durante/GCE_PINNED_RUST_V10.md#repeating-the-collection)

For the prospective run, copy the repository’s `holdout/` directory under `/study`, and retain the shared collector, production reader and the same DSO under `/study` as above. Use a fresh VM and an empty output directory. The protocol pins the systematic difference between the two diagnostic runs to the feature client's `-loop_recurrence_only` option.

```sh
taskset -c 1 python3 /study/holdout/collect_holdout.py --bundle /opt/durante --out /out --cpu 1
```

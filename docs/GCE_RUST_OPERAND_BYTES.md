# Rust clock estimates from latency and memory operand widths

Adding memory operand bytes to the static latency estimate put **9/9 original validation cases within ±20% of native CPU time**, up from 6/9 with latency alone. The same fit missed all three additional algorithms. Even giving a refit on all 18 cases the most favorable values in their observed ranges leaves a worst error of **at least 41.35%**. The byte counter cost an additional **0.86%** of latency-only diagnostic CPU time on this workload mix. [Evaluation](../experiments/results/gce-rust-operand-bytes/evaluation.json) · [Retrospective bound](../experiments/results/gce-rust-operand-bytes/model-limit.json)

## Prediction accuracy

| Features fitted on integer and copy | Original validation | Worst signed validation error | Leave one original program out | Three additional programs |
| --- | ---: | ---: | ---: | ---: |
| Instructions + REP bytes | 2/9 | +68.88% (branch/1) | 6/15 | 2/3 |
| Static latency | 6/9 | +29.20% (branch/4) | 9/15 | 0/3 |
| Static latency + operand bytes | 9/9 | +15.44% (fill/2) | 15/15 | 0/3 |
| Instructions + operand bytes | 0/9 | +92.54% (branch/4) | 9/15 | 0/3 |

The fitted clock used 0.319429 ns per modeled cycle and 0.003405 ns per nominal operand byte. Integer/4 consumed 4.166 times as much native CPU as sort/1. Its median instruction count was just 1.265 times as large, while its modeled latency was 5.015 times as large. MOVS demand was zero and STOS demand was 3,744 bytes throughout. Consequently the REP term in the instruction baseline added a fixed 33.67 ms to each prediction. The CPU's hardware cycle counts, clock speed and IPC were not measured. [Per-case counters and coefficients](../experiments/results/gce-rust-operand-bytes/evaluation.json)

| Additional algorithm | Native CPU median | Latency + byte estimate¹ | Signed error |
| --- | ---: | ---: | ---: |
| Euclidean GCD | 39.58 ms | 62.02 ms | +56.68% |
| Binary lower bound | 35.02 ms | 21.66 ms | −38.15% |
| Rolling window | 15.87 ms | 20.07 ms | +26.42% |

¹ Estimates use the median of each feature. The signed error column uses the largest absolute error among the three individual runs. All 18 cases met the 10% native spread threshold across their six controls. Rolling window fell below the 20 ms duration floor. The GCD and binary-search misses both occur above that floor. [Native controls and repetitions](../experiments/results/gce-rust-operand-bytes/dataset.json.gz)

Using the minimum of six native controls lowered the targets by 0.06–4.58%. After refitting, latency + bytes still passed 9/9 original validation cases, 15/15 original leave-one-program-out cases and 0/3 additional cases. Its worst original validation error became +14.81% and GCD became +57.57%. All cases retained the same native floor and spread status, including when spread was normalized by the minimum. [Minimum sensitivity results](../experiments/results/gce-rust-operand-bytes/evaluation.json)

### Can different coefficients meet ±20%?

After the registered comparison, we allowed the fit to see all 18 program/scale cases and to minimize the **largest** relative error. Against the case medians, the smallest attainable maximum error was 43.02%. We then allowed each case to use any native time and any value of either feature inside its observed ranges, even if those values never occurred together. The lower bound was still 41.35%. At the optimum, the model overestimates fill/2 and GCD and underestimates binary search. This establishes a limitation of the nonnegative two-term model on the observed cases. Determining whether divide latency, data-dependent memory accesses or another cost explains each miss requires a separate experiment. [Exact minimax calculation](../experiments/rust_operand_bytes/model_limit.py) · [Coefficient witnesses](../experiments/results/gce-rust-operand-bytes/model-limit.json)

## Cost and placement

Across 54 matched pairs, latency-only consumed 660.670 CPU seconds and latency with bytes consumed 666.374 seconds (+0.86%). The median increase across pairs was 0.84%. When latency-only ran first the median increase was 0.98%, and when bytes ran first it was 0.78% (27 pairs each). Both modes already instrumented every block for latency. The ratio measures the marginal cost for these executables. It does not measure the cost of enabling latency on an ordinary judge run.

The instrumentation modes differed by at most 93 ppm in instructions and 32 ppm in modeled latency between paired diagnostic runs. Within a case's three byte-enabled runs, instruction spread was at most 22 ppm, modeled latency spread at most 11 ppm and operand-byte spread at most 0.463%. Native CPU spread had a median of 3.79% and maximum of 9.26%. [Raw measurements and paired costs](../experiments/results/gce-rust-operand-bytes/evaluation.json)

The Seoul N2D guest used an AMD EPYC 7B13 with one thread per core. Its scheduler trace matched all 216 children to CPU 1, including 108 same-PID diagnostic loader re-execs. The independent observer polled 5,333 times on CPU 0, the trace recorded zero drops and guest CPU 1 accrued zero steal ticks during collection. VM and boot-disk lookups after teardown both returned HTTP 404. [Placement records](../experiments/results/gce-rust-operand-bytes/placement.json.gz) · [Platform and cleanup receipt](../experiments/results/gce-rust-operand-bytes/platform.json)

GCE used the client source in `5863d42`. A subsequent lifecycle fix clears a pending syscall marker after a returning call. The two release builds passed the same 623-byte hand-counted guest and produced the expected output on all eight archived scale-1 programs in a local CLI check. Cost figures above apply to the recorded GCE build. [Measured implementation](https://github.com/seo-rii/durante/commit/5863d42) · [Lifecycle change](https://github.com/seo-rii/durante/commit/7c78a88)

## Experiment

The proposed clock adds two terms, estimated register-dependency cycles and the sum of decoded memory operand widths. The latter counts the width of a SIMD copy or fill even when the program executes no REP string instruction. Both terms use one production client. An 8-byte read-modify-write contributes 16 bytes. The counter does not observe the addresses accessed, cache traffic, DRAM latency or branch outcomes. [Counter definition](LATENCY_BOUND.md#memory-operand-bytes)

The comparison uses the eight optimized Rust 1.95 executables preserved in the preceding study. It reuses each byte-identical ELF, input and expected output. The five original programs run at three scales. Euclidean GCD, binary lower bound and rolling window each run at one scale. These last three programs have historical timing data and were excluded from every fit. The protocol, workload digests, order, model forms and thresholds were published before this collection. [Frozen protocol](../experiments/rust_operand_bytes/protocol.json) · [Five archived ELFs](../experiments/results/gce-rust-instruction-mix/samples.tar.xz) · [Three transfer ELFs](../experiments/results/gce-rust-instruction-mix/holdout/samples.tar.xz)

For each case, each of three rounds ran a native child before and after two diagnostic children. Both diagnostics used the same client binary and the x86-64-v2 profile. The byte counter was enabled in just one. The order alternated across cases and rounds. Each time observation is the final user plus system CPU time of that child from `wait4`. Each native process executed with the host's CPU features. Each diagnostic used the virtual CPUID profile. These two configurations can select different paths in runtime libraries.

For each model we fitted nonnegative coefficients on the six integer and copy case medians, minimizing squared relative error with no intercept. We scored branch, sort and fill on nine separate cases. A case passes when **all three** diagnostic predictions are within ±20% of the median of its six native controls. The five leave-one-program-out folds omit every scale of the held-out family at once. The minimum-native sensitivity refits every model and scores all predictions against each case's minimum. [Acquisition](../experiments/rust_operand_bytes/collect.py) · [Exact fit and scoring](../experiments/rust_operand_bytes/evaluate.py)

## Replay

From the repository root, the following commands reproduce the fitted weights and scores, read each raw stdout and process record, and check the observed guest CPU placement and teardown.

```sh
study=experiments/rust_operand_bytes
result=experiments/results/gce-rust-operand-bytes
python3 "$study/evaluate.py" "$result/dataset.json.gz" --verify "$result/evaluation.json"
python3 "$study/model_limit.py" "$result/evaluation.json"
python3 "$study/verify_receipts.py" "$result/dataset.json.gz" "$result/receipts.tar.gz"
python3 "$study/verify_placement.py" "$result/dataset.json.gz" "$result/placement.json.gz" --platform "$result/platform.json"
```

The source of the measured production reader is preserved in the receipts archive. Replaying the records uses the reader in the current checkout. The prior study retains the original Rust executables and test inputs. [Paired CPU observations](../experiments/results/gce-rust-operand-bytes/dataset.json.gz) · [Production records and outputs](../experiments/results/gce-rust-operand-bytes/receipts.tar.gz)

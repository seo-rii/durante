# Detecting incomplete first-run profiles during sampling

The guard identified both a completely different hot loop and a shift in frequency between two previously visited loops. It recovered the full diagnostic by rerunning each low-coverage input. Across eight real Aonohako inputs it used **3.36% less parent-plus-child CPU than Full** and **1.34% more than the ordinary v11 live clock**. The controlled workload with a frequency shift cost more. A fresh guarded run and its full rerun together used 59.71 ms of CPU for that input. [Evaluation and observed recoveries](../experiments/results/gce-guarded-sampler/evaluation.json)

## Decisions observed on the guest

| Audit input | Selected / total eligible reads | Returned sample | CPU charged for actual executions |
|---|---:|---|---:|
| Larger input on the original loop | 4,194,719 / 4,197,097 (99.94%) | Guarded | 75.60 ms |
| Switched loop in the same executable | 0 / 4,197,097 (0%) | Full | 167.74 ms total (77.02 ms guarded, 90.72 ms full) |
| Both loops visited, frequencies shifted | 400,000 / 8,000,000 (5%) | Full | 59.71 ms total (29.87 ms guarded, 29.85 ms full) |

Each audit used a fresh guarded execution. The last two rows link to an additional full process, its stdout, clock and full site receipt. Across the 88 balanced held-out comparisons, the in-process selected and omitted counts agreed with the independent full sampler. The six profiles learned on the previous Milan guest also selected the same offsets when the original inputs were repeated on this guest. All 364 runs produced their reference stdout and valid terminal work clocks. The CPU 1 execution trace had no lost events and no observed steal ticks. [Raw measurements](../experiments/results/gce-guarded-sampler/dataset.json.gz) · [Individual reports](../experiments/results/gce-guarded-sampler/receipts.tar.xz)

## CPU cost

| Held-out population | Guarded vs Full | Guarded vs Profiled without the guard | Guarded vs Live |
|---|---:|---:|---:|
| Eight real inputs, 64 matched comparisons | −3.36% | +1.54% | +1.34% |
| Three control inputs, 24 matched comparisons | −12.84% | +8.18% | +10.45% |

These are single-run CPU ratios. Both cost gates passed for the real inputs. If Full and Live can omit their ELF hash preflight, the estimated saving over Full becomes 3.17% and the overhead over Live becomes 1.54%. The controls passed the savings gate against Full and exceeded the overhead limit against Live. On the frequency-shift input Guarded alone used 20.13% more CPU than Live. For the full reporting policy, adding the matched Full cost to each rejected Guarded observation projects a **44.60% increase** versus always running Full on this mix of control inputs. The eight real inputs all passed the coverage threshold, so their pooled saving under the same calculation was 3.36%. The three fresh audits above supply directly observed rerun costs. [Paired CPU and delivery estimates](../experiments/results/gce-guarded-sampler/evaluation.json) · [Guest resource cleanup](../experiments/results/gce-guarded-sampler/cleanup.json)

The guarantee is specific to the fraction of eligible reads. High coverage of those reads can still describe mostly runtime code in an arithmetic-heavy workload. This study qualifies the recovery mechanism on these workloads. Native elapsed-time prediction and contest verdict accuracy require a separate comparison. [Which program work was sampled](GCE_FIRST_RUN_SAMPLER.md#which-work-was-sampled)

## How the guard decides

The first-run study found that another input to the same ELF could move millions of reads outside its saved site list. The guarded client collects the usual address samples at the selected instructions and counts visits to all other eligible read instructions in the main executable. It preserves the original sampler's eligibility rules for integer instructions with an explicit base or index address. REP, SIMD, register-only work and implicit stack accesses have their existing treatment. [First-run results](GCE_FIRST_RUN_SAMPLER.md) · [Eligibility rules](../experiments/access_sample/probe.c)

At basic-block translation the guard groups eligible omitted instructions along straight-line execution, splitting at control flow and at 64 instructions. It increments a counter for the group's size when execution reaches the last omitted instruction. At exit it sums each counter times its group size, checking for overflow. It also rejects interrupted, forked or multithreaded executions under this single-threaded diagnostic contract. The decision requires compatible reports from the same PID. If selected visits account for at least 90% of a positive total, the caller returns the selected report. Otherwise it runs the same ELF and input again under Full and returns the full report. [Guard implementation](../experiments/guarded_sampler/probe) · [Decision](../experiments/guarded_sampler/guard.py)

Two controls test different failure modes. The disjoint loop control takes a branch to a load instruction never visited in calibration. The second control visits both load instructions during calibration, in a 99:1 ratio. Its held-out input changes the ratio to 5:95. Looking for an empty report or for newly translated instructions alone would miss the second change. [Mixed-input source and independent oracle](../experiments/guarded_sampler/control)

## Measurement conditions

The guest runs on Compute Engine with an AMD Milan CPU, one thread per core and two exposed logical CPUs. The tracing controller is pinned to CPU 0. A cpuset and process affinity hold the polling parent and its child on CPU 1. The release uses the x86-64-v2 profile, a 5 ms polling interval, a 100-billion-unit work budget and a 120 s outer timeout. All modes retain the same release v11 meter. [Preregistered protocol](../experiments/guarded_sampler/protocol.json)

Eight balanced rounds run Live, Full, Profiled and Guarded on each of eleven held-out inputs. The primary CPU measure sums the parent process and child user-plus-system time. The guarded parent measurements include ELF hashing, profile lookup, acquisition and the time spent reading and checking the two guard reports. The canonical v11 evidence replay and research export sit outside these timed intervals. CPU and wall time in the guard are the sum of its acquisition and decision intervals. Six prior profiles are checked on a fresh first input, with any differences retained. The six original profiles are used for all subsequent runs. The mixed-path control learns its profile once on this guest. [Registration before measurement](https://github.com/seo-rii/durante/commit/8ff142e3cf826c19b8f87f27281dabe6e84cdb20) · [Cost and recovery criteria](../experiments/guarded_sampler/design.json)

## Replaying a result

Use Python 3.11 or later. The verifier reconstructs the conditional schedule and follows the chosen final report through any actual second invocation. The scorer independently checks the selected offsets, total eligible visits, v11 terminal clocks, paired CPU costs and audit decisions. The optional input archive is the same one used in the first-run study. [Original Aonohako corpus](https://github.com/seo-rii/aonohako/tree/b938afaa6c171a8a56e24d376d6662f93fcea4c6/docs/durante/gce-v11-cpp20-accuracy/corpus)

```sh
study=experiments/guarded_sampler
data=experiments/results/gce-guarded-sampler
python3 "$study/verify.py" "$data" --elf-archive "$corpus/elfs.tar.xz"
python3 "$study/score.py" "$data/dataset.json.gz" "$data/receipts.tar.xz" --verify "$data/evaluation.json"
python3 -m unittest discover -s tests -p 'test_guarded_sampler*.py'
```

The registered guest uses the same v11 release, DynamoRIO 11.3.0-1, Full and Profiled clients as the first-run study. Use the original C++20 ELF archive and build the additional client on Linux x86-64 with the registered GCC 12.2 toolchain. The guest payload materializer verifies its files, including the six profiles learned on the preceding guest. On a disposable two-CPU AMD Milan guest run `sudo taskset -c 0 ./guest.sh` in the resulting payload. [Prerequisite builds and inputs](GCE_FIRST_RUN_SAMPLER.md#replay-and-reproduction) · [Guest collector](../experiments/guarded_sampler/bench.py)

```sh
cmake -S "$study/probe" -B "$tmp/guard-build" -DCMAKE_BUILD_TYPE=Release -DDynamoRIO_DIR="$DynamoRIO_DIR"
cmake --build "$tmp/guard-build"
python3 "$study/prepare.py" "$tmp/payload" --release "$release" --elf-archive "$corpus/elfs.tar.xz" \
  --span "$span_build/libaccess_span.so" --profiled "$fast_build/libsampler_profiled.so" \
  --guarded "$tmp/guard-build/libsampler_guarded.so"
```

## Using the diagnostic on a new executable

The standalone runner accepts an immutable, deterministic single-process, single-threaded ELF that exits successfully with empty stderr. It learns from the first Full invocation and keys the cached profile by executable digest. Later invocations apply the guard. Each call takes a private cache directory and a fresh output directory. In `result.json`, `final_sample` and `stdout` name the selected run's archived files. A fallback captures both runs and checks that they produced the same stdout. A calibration with no eligible reads returns the full sample and creates no profile. [Diagnostic runner](../experiments/guarded_sampler/run.py)

```sh
python3 "$study/run.py" --payload "$tmp/payload" --elf "$elf" --stdin "$first_input" \
  --cache "$tmp/profiles" --out "$tmp/first"
python3 "$study/run.py" --payload "$tmp/payload" --elf "$elf" --stdin "$next_input" \
  --cache "$tmp/profiles" --out "$tmp/next"
```

# Predicting native CPP20 time with the v8 worker

Adding an interaction between load count and simulated branch misses brought one of three new programs within 20% of its native CPU time. A linear model with the same counters passed on the same program. Neither model passed the held-out accuracy and stability gates. Aonohako's v8 worker continues to expose instructions, REP MOVS bytes and REP STOS bytes for ordinary live time limits. [Worker implementation](https://github.com/seo-rii/aonohako/pull/141)

## Blind prediction

The calibration set contained integer arithmetic, branching, sorting, REP copy and REP fill at three scales each. Each target was the median of six native CPU readings. Each case had three native → v8 → native triplets through the HTTP judge on a private Gen2 Cloud Run revision in Seoul with one vCPU. The 214 execution request logs for calibration, blind evaluation and the store/load control all belonged to one instance. Fifteen of fifteen calibration cases passed the 20 ms minimum, and eight passed the 10% native spread limit. [Online run and clock configuration](../experiments/results/cpp20-v8-online.json)

For the supplementary predictors, a separate fixed-ASLR diagnostic replay collected static latency, loads and 14-bit gshare misses. Training used the archived three repeats for the same executable, input and expected output. Fresh executables used the same v6 meter, probe and runtime hashes. Across the eighteen cases, the median instruction counts from these replays and the actual v8 HTTP worker differed by at most 0.090%. Median MOVS and STOS totals matched. The table's three models use the diagnostic replay vectors. Only the separate versioned three-counter policy in the boundary trial ran inside v8. [Diagnostic collection](CPP20_GSHARE_MODEL.md)

The cheap fit used instructions, MOVS bytes and STOS bytes. The linear control added static latency, loads and simulated branch misses. The candidate replaced the branch-miss term with the integer square root of loads multiplied by misses. Every fit used nonnegative coefficients, zero intercept and equally weighted relative errors across the fifteen calibration cases. The interaction is an aggregate statistic. Two programs can have the same value while their loads have different data dependencies. [Frozen fitting protocol](../experiments/results/cpp20-v8-protocol.json)

| New program | Native CPU median | Native spread¹ | Cheap fit | Linear control | Interaction |
| --- | ---: | ---: | ---: | ---: | ---: |
| Binary GCD | 87.431 ms | 17.5% | 36.715 ms | 62.680 ms | 14.093 ms |
| Linked successor search | 195.450 ms | 13.6% | 21.431 ms | 43.008 ms | 36.318 ms |
| Polynomial evaluation | 134.287 ms | 16.6% | 81.054 ms | 153.139 ms | 138.143 ms |

¹ Range of the six uninstrumented CPU readings divided by their median. Every native sample exceeded 20 ms. Each diagnostic counter repeated exactly within each case. The three predictions per program therefore repeat a single prediction. The cheap, linear and interaction fits passed the ±20% accuracy gate on zero, one and one distinct programs, respectively. All three also missed that gate on every case when an entire calibration family was withheld and the other four families were used to fit its coefficients. [Training and predictors](../experiments/results/cpp20-v8-training.json) · [Held-out native readings](../experiments/results/cpp20-v8-labels.json)

The linked search also establishes a limit on this set of features. All six primitive counters were at most those of the scale-2 branching calibration case, and the same ordering holds for the derived square root. Yet the fastest observed linked search took 177.544 ms and the slowest branching run took 99.647 ms. Any predictor nondecreasing in those counters must predict the linked search at or below the branching case. Even using those closest observed times, at least one relative error must reach (177.544 − 99.647) / (177.544 + 99.647), or 28.10%. This inequality was found after scoring. [Executable check](../tests/test_branch_load_interaction.py)

## Live grading

The hosted worker used the previously published v7 three-counter weights rebound to the v8 image for this diagnostic. At 80% of each native median, every native repetition timed out (nine TLEs) and the v8 clock accepted each of the three programs. At 120% every native and live run accepted. The nominal boundaries were 69/105 ms for GCD, 156/235 ms for linked search and 107/162 ms for polynomial evaluation. CPU and wall watchdogs were 90 and 120 seconds. These trial weights are unsuitable for native-millisecond grading on this cohort. A second experiment with one ELF and two inputs shows why a change of weights alone cannot resolve some cases. [Store/load control](../experiments/store_load/README.md)

## Load dependencies

The linked successor holdout repeatedly uses the result of one load as the address of the next. A control built from its permutation follows either one chain or four independent chains on the same thread. Both inputs run the same ELF, read every four-byte index in the 1 MiB table 128 times, and execute four loads, a decrement and a conditional branch in each hot-loop iteration. The program checks the final indices. [Assembly](../experiments/load_chain_control.S)

| Input | Native CPU range, six runs | Median | Ordinary v8 instructions | Native verdict at 300 ms, three runs | Live v8 verdict |
| --- | ---: | ---: | ---: | --- | --- |
| `0`, one chain | 446.196–505.436 ms | 491.736 ms | 56,361,019 | TLE ×3 | AC |
| `1`, four chains | 102.400–113.064 ms | 107.275 ms | 56,361,019 | AC ×3 | AC |

A second private Seoul Cloud Run revision served all 26 requests on one instance. Native CPU includes setup. At 12.05% and 9.94%, respectively, only the four-chain case passed the 10% spread gate. Both inputs produced zero REP MOVS/STOS bytes and the trial v7 weights predicted 13.148 ms for each. Any model making the same prediction for the two inputs incurs at least 64.18% relative error on one median. Even the closest observations, 113.064 and 446.196 ms, imply at least 59.57%. The access order changes between the loops, and these runs did not record hardware cache or stall counters. [Inputs, raw timings, tool hashes and verdicts](../experiments/results/cpp20-load-chain-control.json)

A local replay with the v8 meter and research client agreed on 16 of 19 counters, including 33,816,578 loads and 34 simulated branch misses. The one-chain case had 4.00 times the loop recurrence, 3.62 times the critical path and 3.90 times the optional static latency bound. In the archived v6 diagnostic the linked successor program also had far greater loop recurrence than the scale-2 branching case (1,388,101,548 versus 4,608). The latter pair was misordered even by the combined latency bound. Raw loop recurrence therefore carries information lost when the meter combines recurrence with issue cost. Converting it to native milliseconds still needs a separately fitted model tested on new programs. [Static latency model](LATENCY_BOUND.md)

To reproduce the measured ELF on Debian GCC 12.2.0 and GNU binutils 2.40, set `repo` to the source checkout and run the following in an empty directory. The original unstripped ELF includes its intermediate object's basename.

```sh
cc -c "$repo/experiments/load_chain_control.S" -o cc0JYjty.o
cc -nostdlib -static -no-pie -Wl,--build-id=none cc0JYjty.o -o load-chain
printf '%s  load-chain\n' a44ee37ddab3e7bbebe0bd33d628e551745c897f7e893a2ba243b2efba142dea | sha256sum -c -
printf 0 | ./load-chain
printf 1 | ./load-chain
```

### Measuring just loop recurrence

The research client accepts `-out FILE -loop_recurrence_only` on the patched runtime. It computes the same per-block weight as the full probe and inserts a runtime callback only when a direct conditional self-loop has positive recurrence. It writes a versioned JSON object with `loop_recurrence_cycles_x16`. This mode is for single-threaded, single-process fixtures. The normal live grading page still contains the three ordinary counters.

To compare collection costs, set `sdk` to the prepared DynamoRIO SDK, `meter` to its matching production client and `cpu` to an available logical CPU. Use a fresh output directory for each input. The harness pins the child to that CPU, disables ASLR, checks stdout, and alternates the order of the two collectors. Both configurations enable the production meter's static latency bound. The full research client uses its default set of counters with branch-prediction simulation disabled. The timings include the application and both clients.

```sh
cmake -S "$repo/experiments/time_features" -B probe-build -DCMAKE_BUILD_TYPE=Release \
  -DDynamoRIO_DIR="$sdk/cmake" -DDURANTE_TEST_DRRUN="$sdk/bin64/drrun"
cmake --build probe-build
ctest --test-dir probe-build --output-on-failure
printf 0 > serial.in
printf 'ok\n' > expected
python3 "$repo/experiments/benchmark_loop_recurrence.py" \
  --drrun "$sdk/bin64/drrun" --meter "$meter" --probe probe-build/libtime_features_client.so \
  --program ./load-chain --stdin serial.in --oracle expected --cpu "$cpu" --output serial-report
```

Six pairs per input (120 child executions) reproduced the full probe's recurrence exactly. On a pinned local AMD EPYC 7B12 CPU, median child CPU fell on nine of ten inputs. Binary GCD fell from 1,606 to 977 ms (39.2%), the branching case from 9,879 to 6,085 ms (38.4%) and sorting from 2,174 to 1,734 ms (20.2%). Integer arithmetic increased from 3,303.5 to 3,316.4 ms (0.4%). The median of the ten per-input reductions was 3.84%. These figures measure the diagnostic replay outside Cloud Run. [All paired timings and input hashes](../experiments/results/loop-recurrence-cost.json)

## Reproduce the prediction and scoring

From the repository root, use Python with NumPy and SciPy for fitting. The frozen run used NumPy 1.26.4 and SciPy 1.10.1. Scoring uses the Python standard library. Run both commands with new output filenames.

```sh
python3 experiments/branch_load_interaction.py freeze \
  --input experiments/results/cpp20-v8-training.json --output /tmp/durante-v8-fit.json
python3 experiments/branch_load_interaction.py score \
  --frozen /tmp/durante-v8-fit.json --labels experiments/results/cpp20-v8-labels.json \
  --output /tmp/durante-v8-score.json
```

The prediction file was frozen before the first held-out Cloud Run request. Its SHA-256 was `ab88d6578d6b355a1e8caa0400350c3172622e143002e155455fe6c5bc7e66ff`. The scoring command verifies the executable, input and oracle hashes for each case. To rebuild the three held-out executables, compile each `main.cpp` using the compiler and flags in the manifest. The adjacent `in` and `oracle` files contain the exact stdin and expected stdout. The independent oracle script regenerates the outputs from the inputs. [Frozen corpus](../experiments/branch_load_holdout/manifest.json)

## Prospective L1 data-cache model

We froze two nonnegative weighted sums using the six historical Cloud Run CPU readings for each of the fifteen calibration cases. Both use instructions, REP MOVS/STOS bytes, raw loop recurrence and 14-bit gshare mispredictions. The candidate also uses misses in the cold 32 KiB, eight-way, 64-byte-line virtual data-cache simulation. The two local diagnostic passes shared executable, stdin, expected stdout, fixed address layout and the v8 runtime. All 54 pairs agreed exactly on instructions and typed REP bytes. All ten recorded counters repeated exactly within each input. Unsupported operands were recorded separately. [Fitting protocol](../experiments/results/cpp20-v8-memory/model-protocol.json) · [Counter vectors and tool identities](../experiments/results/cpp20-v8-memory/features.json)

The [three new programs](../experiments/memory_cost_holdout/manifest.json) and their independent oracles were fixed before measurement. Coefficients and predictions were sealed before the new Cloud Run service started. The SHA-256 of the prediction file is `e26b1001a473e22d72ac9dbad5fac463a9063e0e6a62eb488190ffcdd190efcd`. The following predictions were scored offline. The historical v7 weights bound to the v8 image supplied the separate live-clock verdicts. [Frozen predictions](../experiments/results/cpp20-v8-memory/predictions.json)

| New program | Actual native CPU median (range) | Five counters | With L1D misses | Six-counter error |
| --- | ---: | ---: | ---: | ---: |
| Fenwick prefix sums | 40.125 ms (38.859–50.199) | 38.576 ms | 72.788 ms | +81.4% |
| KMP string matching | 71.131 ms (68.711–73.994) | 60.959 ms | 63.623 ms | −10.6% |
| Union-find connectivity | 49.777 ms (49.450–53.626) | 34.925 ms | 57.761 ms | +16.0% |

Both fits passed ±20% on two of the three programs. Fenwick missed the native 10% spread gate. Even the slowest of its six readings was below the candidate prediction by more than 20%. At 48 and 58 ms the candidate predicted TLE and all six native runs accepted. For union-find the candidate correctly predicted TLE at both 40 and 46 ms, where the five-counter fit predicted AC. At the six limits centered on native medians, the two offline models each matched five unanimous native verdicts. The ordinary live clock matched four. The KMP 57 ms run actually stopped on Durante's work budget, with 90 s CPU and 120 s wall watchdogs. The research model met neither the all-program accuracy gate nor the prerequisites for a live grading policy. [CPU triplets, actual verdicts and live receipts](../experiments/results/cpp20-v8-memory/cloud.json)

Adding cache misses barely changed what the model could achieve on the calibration medians. The best possible worst relative error was 5.1941% with five counters and 5.1922% with six. The candidate's relative least-squares fit assigned 2.997 ns per simulated miss and zero to transferred MOVS bytes. The training copy and fill loops stream across cache lines, so misses there closely track bytes transferred. The cache coefficient added 34.5 ms to the Fenwick prediction from its 11,516,798 misses alone. The coefficient is specific to this calibration mix. [Calibration-only bounds and rational certificates](../experiments/results/cpp20-v8-memory/capacity.json)

A subsequent check used only the original training times and the new programs' counts. Two different nonnegative weight vectors both reproduce every training median within 6%. The first predicts 37.14 ms for the same Fenwick input and the second predicts 75.53 ms. They give opposite verdicts at both 48 and 58 ms. These witnesses show why additional calibration workloads need to vary non-REP misses independently of streamed bytes before fitting another cache penalty. [Two feasible weight vectors](../experiments/results/cpp20-v8-memory/fenwick-ambiguity.json)

After the blind trial, three additional native executions per middle-scale calibration family checked how the reference transferred to this instance. Median changes ranged from −21.6% for branching to +0.8% for integer arithmetic. These controls were excluded from fitting. Cloud Logging placed all 69 prospective requests and all 15 control requests on one Seoul instance. [Repeated calibration cases](../experiments/results/cpp20-v8-memory/anchors.json) · [Request placement](../experiments/results/cpp20-v8-memory/placement.json)

From the repository root, the following commands reproduce the published predictions, score the cases, and check calibration capacity. Fitting or recomputing the optimum requires NumPy and SciPy. Scoring uses the Python standard library.

```sh
data=experiments/results/cpp20-v8-memory
python3 experiments/memory_cost_model.py "$data/features.json" --output /tmp/memory-predictions.json
cmp /tmp/memory-predictions.json "$data/predictions.json"
python3 experiments/score_memory_cost.py "$data/predictions.json" "$data/cloud.json"
python3 experiments/memory_cost_capacity.py "$data/features.json" --output /tmp/memory-capacity.json
```

The cache-pass runner `experiments/collect_memory_cache.py --help` lists the inputs for replaying one case. Combine its cache report with the full time-features client (`-bpred`) and a v8 worker meter (`-latency_bound -rep_bytes -cpuid_profile x86-64-v2`) using the same executable path, environment, working directory and disabled ASLR. The full probe source used for this series is the version in commit `a08933762c4a85deb3866ceb7f294835314ce493`. The simulated-cache pass in the prediction file uses the pre-optimization client. [Subsequent REP MOVS optimization and its paired measurements](../experiments/l1d_probe/README.md#long-disjoint-copies)

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

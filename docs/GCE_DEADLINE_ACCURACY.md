# Exact-input clock accuracy on a pinned CPU

These studies compare the live Durante deadline decision with native CPU time for the same trusted static C++20 executable and input. We added threshold runs with different event distributions, dependent successor walks, integer GCD with different divisor distributions, and breadth-first traversal of CSR graphs with different vertex orderings. A Fenwick tree supplies an existing memory-sensitive workload. Each input fixes the ELF hash, stdin and expected stdout. The [primary registration](../experiments/deadline_accuracy/plan.json) and [confirmation registration](../experiments/deadline_accuracy/exact-plan.json) preceded their respective collections.

Each held-out input ran under two clocks at every registered deadline. The baseline reused the earliest calibration admitted by the [memory footprint selector](GCE_FOOTPRINT_ADMISSION.md), paying for a native observation when it needed a new anchor. Both arms shared a full profile. Each enforcement launched a fresh process, used the Durante counter to enforce the selected clock and had a separate 120-second watchdog. Nine direct native runs per input followed all decisions, on CPU 1 of an AMD Milan GCE VM with one thread per core. The reference is child user plus system CPU from wait4. A false accept means all nine exceeded the limit but the metered run finished. A false timeout means all nine finished within the limit but the clock exceeded it. The tables count mixed references separately. Fixed limits were specified in milliseconds before collection. Adaptive limits were computed only from the baseline estimate. A limit belonging to both groups counts once in the total.

## Calibrating deadlines near the footprint estimate

For an estimate within 25% of the requested limit, the first study used the median of three native observations of that exact input. A paid observation was reusable only for the same physical capture, boot and pinned CPU. The other deadlines used the baseline calibration. All 15 holdouts reached the near band at least once.

| Deadline group | Native unanimous | Baseline correct | Near median correct | Mixed native |
|---|---:|---:|---:|---:|
| All | 80 | 73 | 77 | 9 |
| Fixed | 13 | 12 | 12 | 2 |
| Near | 41 | 36 | 40 | 9 |
| Far | 39 | 37 | 37 | 0 |

False accepts fell from six to three and false timeouts from one to zero. The four corrections occurred on adaptive limits for three prospective inputs. Majority agreement, including mixed limits, rose from 76/89 to 83/89. On the 15 holdouts, the baseline used 4.437 s of target CPU for profiles and 0.967 s for native calibrations. The median rule paid for 37 additional native runs (3.331 s). These are preparation costs, excluding live enforcement, the five starting calibrations and the independent references. [Actual decisions and costs](../experiments/results/gce-deadline-accuracy/metrics.json)

For threshold_runs/random the baseline estimated 14.72 ms while all nine references took 78.095–78.395 ms. The median rule corrected the verdict at 15 and 17 ms, but the 23 and 75 ms deadlines were outside its band. For the 11 MiB Fenwick input, the three observations were 168.310, 180.316 and 166.277 ms. The median allowed completion at 169 ms. All nine subsequent native references exceeded it (177.187–183.865 ms). These data identify the effect of transferring calibrations across inputs and the variability of individual observations. They do not isolate a microarchitectural cause.

Replaying predictions offline, the first exact-input observation alone also matched 40/41 unanimous near decisions and accounted for all four corrections. Across all 15 packets, median absolute duration error was 7.32% for the baseline, 1.32% for the first observation and 1.07% for the three-observation median. This exploratory calculation motivated a new live confirmation on fresh inputs. [Ablation](../experiments/results/gce-deadline-accuracy/ablation.json) · [Primary raw records](../experiments/results/gce-deadline-accuracy/output.tar.xz)

## One native observation per exact input

A fresh guest tested the first pinned native observation at every deadline for that input. The input set comprised ten previously unmeasured ELF/stdin pairs and two earlier error controls, using the same five starting calibrations by input identity, newly measured on this guest. The five programs were already known. Native references were held out, and both arms actually ran for all 60 deadlines. [Registered rule](../experiments/deadline_accuracy/exact-rules.json) · [Confirmation archive](../experiments/results/gce-deadline-exact-one/output.tar.xz)

| Holdouts and deadlines | Unanimous | Baseline correct | Exact correct | Mixed | Majority correct, baseline → exact |
|---|---:|---:|---:|---:|---:|
| 10 new inputs | 36 | 32 | 36 | 14 | 41/50 → 44/50 |
| 2 earlier error controls | 8 | 4 | 8 | 2 | 6/10 → 10/10 |
| All 12 inputs | 44 | 36 | 44 | 16 | 47/60 → 54/60 |
| Fixed limits, all inputs | 18 | 15 | 18 | 6 | 19/24 → 22/24 |
| Adaptive limits, all inputs | 26 | 21 | 26 | 10 | 28/36 → 32/36 |

Seven false accepts and one false timeout were corrected on three inputs for the same threshold-runs executable. On the new larger random stream, all nine references took 126.260–127.550 ms. The baseline accepted it at 24, 28 and 75 ms. With short runs on a smaller array, the baseline timed out at 12 ms despite native times of 10.410–10.894 ms. Both clocks agreed at all unanimous deadlines for the other four programs. On the new 7.5 MiB Fenwick input at 184 ms, the exact observation was 181.911 ms and five later native runs out of nine exceeded the limit. Changing the verdict to a pass lost one majority match. The earlier 11 MiB control at 169 ms was also mixed on this guest, with five of nine native runs over the limit. [Per-input timings and actual verdicts](../experiments/results/gce-deadline-exact-one/metrics.json)

On the ten new inputs, estimates within ±20% of the independent native median rose from 8/10 to 10/10. Median absolute error fell from 8.40% to 2.21% and worst absolute error from 81.24% to 16.38%. Across all 12 holdouts, median absolute error fell from 8.40% to 1.75%. Each pair reused its single calibration across deadlines. Seven holdouts already had a native observation of that same capture. The other five added 0.525 s of target CPU in total, above 3.435 s of shared full profiles and 0.650 s of baseline native calibrations. The nine independent rounds per input cost 10.610 s and are excluded from those preparation costs.

These results support exact-input calibration for trusted, repeatable static executables when a native preparation run is affordable. It ties the scale to the chosen host and to that particular observation. Reusing the same packet preserves the chosen scale. A new calibration can produce a different scale, especially close to a deadline. The experiment covers one CPU family, five C++ programs and a complete preflight of each target input. General submission judging, managed runtimes and portability across machines require separate qualification.

## Counter conformance and Aonohako replay

A third independent collection paired the released client and the restored live-counter path from Durante #88 on all 20 primary inputs, three executions per client. All 120 final live pages matched their terminal instruction, MOVS and STOS totals. All 20 per-input instruction medians matched across builds and REP counts matched in all repetitions. For the small-block successor walk, one candidate execution counted 68,401,565 instructions versus 68,401,549 for the other five. The other 19 inputs had identical totals in all six runs. This measures counter agreement. The native-time comparisons above used the registered released client. [Counter registration](../experiments/deadline_accuracy/client-parity.json) · [Per-run counts](../experiments/results/gce-deadline-client-parity/metrics.json)

Aonohako's Go reference command reproduced all 178 actual metered decisions from the first study and all 120 from the confirmation. All 54 sets of input, policy and deadlines received integer clock projections. This verifies consumer agreement with recorded live decisions. Native accuracy uses the independent wait4 observations above. The three guests had zero recorded steal ticks, and their collection receipts confirm removal of their VM, boot disk and temporary key. [Primary Go replay](../experiments/results/gce-deadline-accuracy/aonohako.json) · [Confirmation Go replay](../experiments/results/gce-deadline-exact-one/aonohako.json)

## Reproduce

From a Durante checkout, reconstruct the reports from the compressed raw observations. The verifiers check the corresponding registration, native references, live or terminal records and per-PID CPU placement.

```sh
study=experiments/deadline_accuracy
scratch=$(mktemp -d)
for pair in "gce-deadline-accuracy near-three" "gce-deadline-exact-one exact-one"; do
  set -- $pair
  data=experiments/results/$1
  mkdir "$scratch/$1"
  tar -xJf "$data/output.tar.xz" -C "$scratch/$1"
  python3 -B "$study/verify.py" "$scratch/$1/output" --strategy "$2" --output "$scratch/$1/metrics.json"
  cmp "$scratch/$1/metrics.json" "$data/metrics.json"
done
data=experiments/results/gce-deadline-client-parity
mkdir "$scratch/parity"
tar -xJf "$data/output.tar.xz" -C "$scratch/parity"
python3 -B "$study/client_parity.py" --verify --out "$scratch/parity/output" --metrics "$scratch/parity/metrics.json"
cmp "$scratch/parity/metrics.json" "$data/metrics.json"
```

With the registered ELFs and an Aonohako build containing the `durante-reference` input-deadline command, reproduce the consumer comparison on either extracted accuracy archive. To rerun the guest study, use `prepare.py` with the registered guarded-sampler runtime and temporal probe. The parity payload takes the registered candidate client via `--candidate` in place of the probe. [Runtime and sampler setup](GCE_SHAPE_CALIBRATION.md) · [Go reference CLI](https://github.com/seo-rii/aonohako/pull/155)

```sh
python3 -B "$study/replay_aonohako.py" "$scratch/gce-deadline-exact-one/output" \
  --strategy exact-one --binaries "$binaries" --reference "$aonohako_reference" \
  --output "$scratch/go.json"
cmp "$scratch/go.json" experiments/results/gce-deadline-exact-one/aonohako.json
python3 -B "$study/prepare.py" "$scratch/guest" --mode exact --runtime "$runtime" --probe "$probe"
```

For a trusted input with a completed footprint capture, reuse one packet directory per capture and strategy, and supply a fresh output directory for each enforcement. Pin both preparation and execution to the same CPU. The packet binds the chosen policy to the exact ELF, stdin, full capture, initial policy and boot. The CLI returns 0 for completion, 123 for a metered timeout, 124 for the watchdog, and 1 for unavailable evidence.

```sh
taskset -c "$cpu" python3 -B "$study/gate.py" --strategy exact-one \
  --payload "$runtime" --elf "$binary" --stdin "$input" --capture "$capture" \
  --selection "$selection" --policy "$policy" --packet-dir "$scratch/exact-packet" \
  --out "$scratch/enforcement" --limit-ms 75
```

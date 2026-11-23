# Exact-input clock accuracy on contest programs

The previous contest study found that the first native observation exceeded the later reference median on all twelve held-out inputs. This study tests a change to the observation used for the Durante clock scale. It adds three previously unmeasured contest programs and includes the Wall and Money for Nothing inputs whose previous deadline verdicts disagreed with the native majority. [Earlier study](GCE_CONTEST_DEADLINES.md) · [Frozen scoring and execution rules](../experiments/contest_warm/rules.json)

| New contest problem | Measured algorithm | Independent answer check |
|---|---|---|
| [IOI 2012 Crayfish Scrivener](https://ioinformatics.org/files/ioi2012problem3.pdf) | Persist each typed letter with a parent and jump pointer. Undo by restoring an earlier command version and answer position queries by climbing ancestors. | Build the command-version tree offline and answer queries from the path during a depth-first traversal. Small tests keep full copies of the edited strings. |
| [IOI 2013 Dreaming](https://ioi.te.lv/locations/ioi13/contest/day1/dreaming/dreaming.pdf) | Traverse each weighted component to find its diameter and minimum vertex radius, then compute the minimum diameter after connecting the forest. | Compute eccentricities by rerooting, explicitly connect the chosen centers, and measure the completed tree's diameter. Small forests enumerate possible new trails. |
| [ICPC World Finals 2017 Posterize](https://icpc.global/worldfinals/problems/2017-ICPC-World-Finals/icpc2017.pdf#page=11) | Partition consecutive histogram levels with dynamic programming. Represent each group by its nearest integer weighted mean. | Optimize directly over the available palette values, charging each interval to its nearest endpoints. Exhaustive small palettes and large pixel weights exercise rounding and 64-bit costs. |

Four fresh inputs per program vary editing history and query distance, forest topology, or palette size. Inputs to Dreaming contain up to 100,000 vertices. Both IOI executables follow their sample grader's text interface. All target runs include parsing, the complete solution and its output. Input and expected-output generation occur separately. The two previous controls reuse the identical ELF, stdin and expected stdout. [Registered hashes and deadlines](../experiments/contest_warm/plan.json)

## Consecutive native calibration

Each input gets one complete Durante profile, then four distinct direct native executions on the same guest boot and pinned CPU. Each produces a normal policy binding its wait4 CPU time to the same completed work count. The baseline selects observation 0. The candidate sorts observations 1, 2 and 3 by CPU time, preserving their order on ties, and selects the median. Each policy sets the scale for fresh Durante executions at every limit. The immutable calibration packet can serve multiple deadlines for the same capture.

The fixed whole-millisecond limits and adaptive limits of 85%, 100% and 115% of the baseline estimate were published before collecting timings. Adaptive limits round up, and overlapping limits are enforced once per arm. Both clock executions are fresh processes with alternating arm order. Each has a separate 120 s watchdog. After all clock decisions, the guest runs eleven independent direct native reference rounds in rotating order. The primary endpoint is median absolute relative error against the per-input native median over the twelve prospective inputs. Fixed and adaptive deadline agreement, native-majority disagreement and calibration CPU cost are separate outcomes. These runs use the registered v11 counter on AMD Milan GCE with a single hardware thread per core.

The replication used the same payload on a fresh VM. The replication rule, registered before looking at the first VM's results, requires a lower primary error on each independent boot. Each table entry compares the first exact-input calibration with the candidate. A smaller duration error is better. Verdict counts show how many distinct input/deadline pairs agreed with independent native executions. [Replication rule](../experiments/contest_warm/replication.json)

| Prospective inputs per boot | Median absolute error | Maximum absolute error | Inputs improved / tied / worsened | Correct on unanimous limits | Correct by majority | Correct by majority, fixed limits |
|---|---:|---:|---:|---:|---:|---:|
| Boot 1, 12 | 2.28% → 2.64% | 4.00% → 12.43% | 4 / 0 / 8 | 69 → 68 of 69 | 75 → 72 of 76 | 47 → 47 of 48 |
| Boot 2, 12 | 4.54% → 3.18% | 10.25% → 7.51% | 11 / 0 / 1 | 72 → 72 of 72 | 77 → 78 of 78 | 47 → 48 of 48 |

On the first boot, all eleven native runs of Scrivener's distant versions finished within 13 ms (11.055–12.060 ms). The first calibration measured 12.027 ms and passed. The consecutive median measured 13.095 ms and timed out. At 12 ms only one of the eleven native executions timed out on each boot. The candidate passed this limit only on the second boot. Across the 48 identical prospective fixed limits, both the native majorities and the baseline's decisions reproduced across boots. The primary endpoint moved in opposite directions. [First boot and per-input results](../experiments/results/gce-contest-warm/analysis.json) · [Replication](../experiments/results/gce-contest-warm-repeat/analysis.json)

For the two previously measured controls, the median absolute error fell from 0.95% to 0.35% on boot 1 and from 0.38% to 0.10% on boot 2. Both arms matched the native majority on every control deadline (12 and 11 respectively). Each boot required 36 extra native runs for the prospective inputs, costing 431 and 420 ms of target CPU. The archived analysis also includes the two registered offline projections. Aonohako's Go reference matched all 176 and 178 actual work-clock outcomes, including both arms and the controls. [Boot 1 Go replay](../experiments/results/gce-contest-warm/aonohako.json) · [Boot 2 Go replay](../experiments/results/gce-contest-warm-repeat/aonohako.json)

## Interleaved native calibration

The first consecutive-run result motivated a second preregistration on ten fresh inputs, two each for Dreaming, Posterize, Scrivener, Wall and Money for Nothing. They vary forest topology, histogram weights, editor history, interval updates and market geometry. Two earlier inputs recur as controls, including the 13 ms Scrivener case. The five ELFs run unchanged. All solutions and inputs have independent answer checks. [Inputs, hashes and fixed deadlines](../experiments/contest_batch/plan.json) · [Rules filed before measurement](../experiments/contest_batch/rules.json)

Each guest first collects the complete profile and the initial exact native payment for each input. Three additional native rounds then visit every input, rotating the start of the order sorted by case ID digest. For each input the candidate selects its own median of the three later payments. Each limit gets two new Durante executions with alternating arm order. Eleven subsequent rotating native rounds provide the independent reference. This evaluates the complete calibration procedure. The additional runs also occur at a different time relative to the reference, so the effect of interleaving alone cannot be deduced. Adaptive deadlines depend on the first measurement on each guest. Fixed deadlines allow a like-for-like comparison across guests.

| Prospective inputs per boot | Median absolute error | Maximum absolute error | Inputs improved / tied / worsened | Correct on unanimous limits | Correct by majority | Correct by majority, fixed limits |
|---|---:|---:|---:|---:|---:|---:|
| Boot 1, 10 | 2.24% → 0.68% | 34.62% → 3.58% | 10 / 0 / 0 | 61 → 63 of 63 | 65 → 67 of 67 | 39 → 40 of 40 |
| Boot 2, 10 | 1.17% → 0.44% | 4.56% → 4.45% | 8 / 0 / 2 | 59 → 59 of 59 | 64 → 64 of 64 | 40 → 40 of 40 |

The interleaved median reduced the primary duration error on both boots. The median signed error moved from +2.24% to +0.68% on boot 1 and from +1.08% to +0.015% on boot 2. The ten prospective inputs had native medians of 0.924–30.588 ms across the guests. Three extra native calibrations per input added 403 and 400 ms of target CPU per ten inputs. The complete profiles cost 5.24 s per boot and the first native payments cost 142 and 133 ms respectively. [First boot and per-input results](../experiments/results/gce-contest-batch/analysis.json) · [Replication](../experiments/results/gce-contest-batch-repeat/analysis.json)

Both prospective verdict corrections came from Scrivener's prefix forks on boot 1. The first sample measured 15.816 ms. The interleaved median measured 11.868 ms. All eleven independent references finished in 11.249–11.941 ms, so the interleaved clock correctly passed both 12 and 14 ms. On boot 2 the first sample already measured 11.782 ms. The two arms made identical live decisions on every input. Comparing the same 40 prospective fixed limits across boots, the native majorities and the candidate's decisions reproduced on all 40. Four prospective limits had mixed native votes on boot 1 and five on boot 2. Per-input vote counts and margins are available for every deadline. [Boot 1 metrics](../experiments/results/gce-contest-batch/metrics.json) · [Boot 2 metrics](../experiments/results/gce-contest-batch-repeat/metrics.json)

The two earlier controls had median absolute error of 3.28% → 0.52% and 2.15% → 2.71%. On the first guest the candidate also corrected a false timeout on Scrivener's distant versions at 12 ms, taking majority agreement from 12 to 13 of 14. Both arms scored 13/14 on the second guest. Wall's crossed intervals still timed out at 12 ms on both guests, with only 3/11 and 5/11 native executions exceeding the limit. This is a remaining mixed-reference disagreement. With controls included, Aonohako's Go reference matched all 162 and 156 Durante executions. [Boot 1 Go replay](../experiments/results/gce-contest-batch/aonohako.json) · [Boot 2 Go replay](../experiments/results/gce-contest-batch-repeat/aonohako.json)

Use the interleaved operator for further measurements of trusted, already profiled batches. The results establish a repeatable gain in median duration accuracy on this cohort and CPU class. The second guest shows how the verdict gain depends on how close an input's runtime is to the deadline. Qualification for arbitrary submissions, longer-running workloads, other languages and other CPUs is still open.

## Reproduce the accuracy and verdict counts

Run from the Durante checkout. Each archive contains the registered input bytes, four native calibrations per input, the live deadline receipts, eleven later native observations and the ordered kernel execution trace. The Python replays additionally regenerate the expected answers and independently reconstruct the median, limits and actual verdicts.

```sh
set -eu
scratch=$(mktemp -d)
for study in warm batch; do
  case "$study" in
    warm) strategy=warm-three ;;
    batch) strategy=interleaved-three ;;
  esac
  for suffix in '' '-repeat'; do
    name="gce-contest-$study$suffix"
    result="experiments/results/$name"
    dest="$scratch/$name"
    mkdir "$dest"
    tar -xJf "$result/output.tar.xz" -C "$dest"
    python3 -B experiments/deadline_accuracy/verify.py "$dest/output" \
      --strategy "$strategy" --output "$dest/metrics.json"
    python3 -B experiments/contest_warm/analysis.py "$dest/output" \
      --strategy "$strategy" --output "$dest/analysis.json"
    cmp "$result/metrics.json" "$dest/metrics.json"
    cmp "$result/analysis.json" "$dest/analysis.json"
  done
done
python3 -B -m unittest discover -s tests -p 'test_contest*.py' -v
```

To rebuild the same five static executables, supply the registered runtime and temporal probe. The builder checks the ELF digests. Both studies use the same binaries. The archived collection sources correspond to commits `7d66a4c` for consecutive and `192440b` for interleaved calibration. Each collection record gives the exact file hashes. Set `reference` to Aonohako's compiled reference CLI to replay the archived decisions. [Runtime and probe](GCE_SHAPE_CALIBRATION.md) · [Aonohako reference](https://github.com/seo-rii/aonohako/pull/155)

```sh
python3 -B experiments/calibrated_limits/prepare.py "$scratch/guest" \
  --study experiments/contest_batch --source-dir experiments/contest_warm \
  --source-dir experiments/contest_accuracy --runtime "$runtime" --probe "$probe"
for study in warm batch; do
  case "$study" in
    warm) strategy=warm-three ;;
    batch) strategy=interleaved-three ;;
  esac
  python3 -B "experiments/contest_$study/corpus.py" --binaries "$scratch/guest/binaries" > "$scratch/$study-plan.json"
  cmp "$scratch/$study-plan.json" "experiments/contest_$study/plan.json"
  for suffix in '' '-repeat'; do
    name="gce-contest-$study$suffix"
    dest="$scratch/$name"
    python3 -B experiments/deadline_accuracy/replay_aonohako.py "$dest/output" \
      --strategy "$strategy" --binaries "$scratch/guest/binaries" --reference "$reference" --output "$dest/go.json"
    cmp "$dest/go.json" "experiments/results/$name/aonohako.json"
  done
done
```

### Prepare packets on a pinned worker

These operators target trusted static executables with deterministic output and a completed footprint capture. Each capture needs its own first exact native payment on the current CPU and boot. For consecutive sampling, use the ordinary gate with `--strategy warm-three` and a new packet directory. For interleaved sampling, prepare a batch of at least two distinct ELF/input pairs using an ordered JSON array. Paths resolve relative to the items file; each ID also names its reusable packet directory. [Full capture and gate setup](GCE_DEADLINE_ACCURACY.md)

```json
[
  {"id":"dreaming/comb_islands","elf":"bin/dreaming","stdin":"inputs/forest","capture":"captures/forest"},
  {"id":"scrivener/prefix_forks","elf":"bin/scrivener","stdin":"inputs/editor","capture":"captures/editor"}
]
```

```sh
set -eu
taskset -c "$cpu" python3 -B experiments/contest_batch/batch.py --items items.json --out "$batch"
packet="$batch/dreaming/comb_islands"
taskset -c "$cpu" python3 -B experiments/deadline_accuracy/gate.py --strategy warm-three \
  --elf bin/dreaming --stdin inputs/forest --capture captures/forest \
  --selection captures/forest/baseline-selection.json --policy captures/forest/policy.json \
  --batch-manifest "$batch/manifest.json" --packet-dir "$packet" --payload "$runtime" --out "$new_result" --limit-ms 25 --timeout-ms 120000
```

Reusing the same ordered items and complete batch verifies the manifest without buying new native observations. Passing the batch manifest requires a published member and checks its binding to the current capture, ELF, stdin, boot and CPU. The Durante work budget enforces the selected limit and the wall timeout serves as an independent guard.

The [larger-input study](GCE_CONTEST_SCALE_CLOCK.md) tests the same interleaved calibration on six new contest inputs and records when the three calibration samples straddle a requested limit.

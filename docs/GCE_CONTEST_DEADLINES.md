# Deadline accuracy on IOI and ICPC World Finals problems

The earlier exact-input study corrected eight unanimous deadline errors on one branch-heavy executable. This study carries its two clocks onto four previously unmeasured contest solutions. Each program starts with one new calibration input, followed by three fresh holdouts. All solutions, inputs and independent answer generators in this suite were written for the experiment. [Prior study](GCE_DEADLINE_ACCURACY.md) · [Preregistered inputs, outputs and binary hashes](../experiments/contest_accuracy/plan.json)

| Contest problem | Timed solution | Independent check and input variations |
|---|---|---|
| [IOI 2011 Rice Hub](https://ioinformatics.org/files/ioi2011problem3.pdf) | Binary search on collection size, prefix sums for median transport cost | A sliding window maintains the median cost incrementally. The distributions exercise duplicate coordinates, irregular gaps and 64-bit budgets. Small roads enumerate hub positions and choose the cheapest fields. |
| [IOI 2014 Wall](https://ioinformatics.org/files/ioi2014problem2.pdf) | A lazy segment tree composes ordered range lower and upper bounds | A flat array applies each update directly. The four layouts use the same 16,384 columns and 25,000 updates, with aligned, crossed, short and nested intervals. The regression tests also check single columns and the maximum height. |
| [ICPC World Finals 2017 Money for Nothing](https://icpc.global/worldfinals/problems/2017-ICPC-World-Finals/icpc2017.pdf#page=7) | Sort and prune dominated offers, then find profitable pairs by monotone divide and conquer | On piecewise-linear buyer frontiers the generator evaluates the two available bids around each quadratic maximum. Each market has 16,000 producers and 16,000 buyers. Small arbitrary markets are checked by trying every pair. |
| [ICPC World Finals 2018 Catch the Plane](https://icpc.global/worldfinals/problems/2018-ICPC-World-Finals/icpc2018.pdf) | Reverse-time event DP for stochastic journeys, processing simultaneous departures together | Station-local departure lists, binary search and exact rational probabilities check layered routes, simultaneous departures, equal-time missed transfers and scattered services. Small schedules enumerate next-bus choices. |

Both ICPC programs accept the published text format. Wall reproduces the sample grader's text input and prints every final height. Rice Hub's driver reads the problem parameters and positions, calls `besthub`, and prints the returned number of fields. The historical sample grader's extra expected-answer line belongs to the harness. Catch the Plane's contest tolerance is 10⁻⁶. Registered outputs also require this binary's exact nine-decimal rendering of the independently calculated probabilities. The contest input parser and all solution output run inside the measured process. Python generators and answer oracles run outside it.

## Study design

The guest runs the entire input through the registered production counter and temporal probe to select a footprint-compatible calibration. The baseline can borrow the earliest compatible calibration from the same executable or pay for a new one. The candidate uses the first native observation of this exact input, reusing any observation already paid for this capture, CPU and boot. Each input/deadline/arm then receives a fresh actual metered execution. A separate 120-second wall watchdog limits the experiment. The fixed limits of 4, 12 and 32 ms and baseline-relative limits of 85%, 100% and 115% were frozen in advance. Relative limits round up to whole milliseconds and duplicate limits are enforced once per arm. The limits are diagnostic and deliberately probe the observed scale of the solutions. [Registered scoring and execution rules](../experiments/contest_accuracy/rules.json)

Only after completing all clock decisions does the guest collect nine direct native executions per holdout, rotating the order of the 12 inputs on CPU 1 of an AMD Milan GCE VM with one thread per core. We use each child's user plus system CPU time. A unanimous error is a metered pass when all nine exceeded the limit, or a metered timeout when all nine met it. A mixed reference has direct runs on both sides of the limit. Majority agreement includes those mixed cases. Per-input duration errors use the native median.

## Measured results

Both arms chose the same exact-input native observation for every holdout. The footprint selector tried 24 prior-anchor comparisons. Each comparison reported insufficient material load-site coverage, and five also reported a change in load-site shares. Consequently the baseline paid for all 12 inputs and the exact arm had zero additional native payments. The results quantify the accuracy and cost of an exact-input clock on these solutions. These runs did not exercise a successful transfer between contest inputs. [Per-case metrics](../experiments/results/gce-contest-deadlines/metrics.json)

| Problem (three holdouts each) | Distinct limits | Correct / unanimous, both clocks | Mixed | Correct / all by native majority |
|---|---:|---:|---:|---:|
| Rice Hub | 12 | 12/12 | 0 | 12/12 |
| Wall | 17 | 14/14 | 3 | 16/17 |
| Money for Nothing | 17 | 16/16 | 1 | 16/17 |
| Catch the Plane | 18 | 18/18 | 0 | 18/18 |
| All | 64 | 60/60 | 4 | 62/64 |

Both clocks agreed with all 35 unanimous fixed limits and all 29 unanimous adaptive limits. Four limits belonged to both groups. The shared estimates fell within ±20% of the native median on 12/12 inputs. The median absolute relative error was 1.21% and the worst was 5.78%. The calibration estimate exceeded the later median on all 12 inputs; this design does not identify the cause. These were short runs with native medians from 3.519 to 11.976 ms.

At the fixed 12 ms limit on Wall's crossed intervals, the calibration was 12.113 ms, and the nine later references spanned 11.923–14.824 ms with a median of 11.976 ms. At the adaptive 6 ms limit on Money's dense scrambled market, the calibration was 6.300 ms and the references spanned 5.908–6.174 ms. At both limits each metered arm timed out and only three of nine native runs exceeded the limit. The other two mixed cases were the same Wall input at 13 and 14 ms, where both clocks agreed with the majority.

The 12 holdout profiles used 4.937 s of target CPU and their native calibrations used 0.080 s. The four seeds used another 1.629 s for profiles and 0.023 s for native calibration. The 108 subsequent native references used 0.714 s. These figures exclude controller CPU and live enforcement. The kernel trace accounted for all 268 measured PIDs on CPU 1 with zero steal ticks. All 128 real metered decisions were covered by Aonohako's Go reference replay, comprising 40 live kills and 88 normal completions. The temporary VM, boot disk and SSH key were removed. [Raw observations](../experiments/results/gce-contest-deadlines/output.tar.xz) · [Go decisions](../experiments/results/gce-contest-deadlines/aonohako.json) · [GCE and cleanup record](../experiments/results/gce-contest-deadlines/collection.json)

### Exploratory replay of a single seed per executable

To examine what the conservative admission rule avoided, an offline comparison applies the first seed's original clock weights to each holdout's completed counters. It uses the same recorded deadlines, including the adaptive limits selected by the actual baseline. This comparison predicts 59/60 unanimous outcomes, 35/35 unanimous fixed outcomes and 62/64 native majorities. It keeps all 12 estimates within ±20%, with median absolute error of 3.98% and worst error of 9.23%. These are predictions from the completed execution. Only the two registered arms were enforced live. [Seed replay and refusal reasons](../experiments/results/gce-contest-deadlines/seed.json)

For Catch the Plane's tight transfers, the seed-only prediction was 4.989 ms at the 5 ms limit. Every native run exceeded the limit (5.459–5.673 ms), as did both live clocks. For Wall's crossed intervals the seed predicted completion at 12 ms, agreeing with the native majority on that mixed case. The same majority score therefore covers different errors.

## Reproduce

From the Durante checkout, extract the archive to an empty directory and verify the registered input hashes and all recorded decisions. The small correctness tests compile and run the solvers. The archive tests replay the actual guest evidence locally.

```sh
data=experiments/results/gce-contest-deadlines
tmp=$(mktemp -d)
tar -xJf "$data/output.tar.xz" -C "$tmp"
python3 -B experiments/deadline_accuracy/verify.py "$tmp/output" --contest --output "$tmp/metrics.json"
cmp "$tmp/metrics.json" "$data/metrics.json"
python3 -B experiments/contest_accuracy/seed_replay.py "$tmp/output" --output "$tmp/seed.json"
cmp "$tmp/seed.json" "$data/seed.json"
python3 -B -m unittest discover -s tests -p 'test_contest*.py' -v
```

To rerun collection, package the registered runtime, probe and the four static ELFs using the common builder. With the resulting binaries and Aonohako's reference CLI, replay the recorded counter values against the certified input-specific deadlines. [Runtime setup](GCE_SHAPE_CALIBRATION.md) · [Aonohako reference](https://github.com/seo-rii/aonohako/pull/155)

```sh
python3 -B experiments/calibrated_limits/prepare.py "$tmp/guest" \
  --study experiments/contest_accuracy --runtime "$runtime" --probe "$probe"
python3 -B experiments/contest_accuracy/corpus.py --binaries "$tmp/guest/binaries" > "$tmp/plan.json"
cmp "$tmp/plan.json" experiments/contest_accuracy/plan.json
python3 -B experiments/deadline_accuracy/replay_aonohako.py "$tmp/output" --contest \
  --binaries "$tmp/guest/binaries" --reference "$aonohako_reference" --output "$tmp/go.json"
cmp "$tmp/go.json" "$data/aonohako.json"
```

The [follow-up calibration study](GCE_CONTEST_WARM_CLOCK.md) adds three contest programs and compares two ways to repeat exact-input measurements on fresh CPU-pinned guests.

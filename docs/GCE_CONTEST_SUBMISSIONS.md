# Forecasting CPU time for a new contest submission

Rice Hub and Crayfish Scrivener each have two C++ implementations in this experiment. They receive byte-identical inputs and must produce the same answer. The original solution supplies an optional calibration. The second solution is the held-out submission whose native time the clocks predict. The earlier exact-input experiment paid for a native run of the submitted executable itself. [Registered design and ELF hashes](../experiments/contest_submissions/plan.json) · [Exact-input results](GCE_CONTEST_SCALE_CLOCK.md)

| Problem | Reference implementation | Held-out implementation | Inputs |
|---|---|---|---|
| IOI 2011 Rice Hub | Binary-search the feasible window length using prefix sums. | Slide one window over the sorted fields and use prefix sums to price each window in constant time. | Uniform spacing, duplicate clusters, scattered gaps, wide coordinates |
| IOI 2012 Crayfish Scrivener | Store one parent and one jump per letter. | Store power-of-two ancestors for each letter and use binary lifting for queries. | Recent queries, distant versions, interwoven pages, bookmarked chapters |

For each input the two executables run to completion under the v11 diagnostic meter. We compare two frozen work formulas from Aonohako's Milan C++ study. The first combines instructions and REP bytes. The second combines the static latency bound and nominal memory-operand bytes. Each yields an absolute forecast in nanoseconds. Each also yields a second forecast by multiplying the reference solution's median native CPU time by the ratio of the submission's work to the reference's work. The original C++ weights were fitted on integer and REP-copy programs. [Weights and arithmetic](../experiments/contest_submissions/score.py) · [Original fit](https://github.com/seo-rii/aonohako/tree/b938afaa6c171a8a56e24d376d6662f93fcea4c6/docs/durante/gce-v11-cpp20-accuracy)

Each of two fresh AMD Milan GCE guests pins all measured processes to CPU 1, with one hardware thread per core. After profiling both solutions the collector makes three round-robin native passes over the reference solutions. It writes all four predictions for all eight inputs and the digest of the preceding observations. Only then does it collect nine rounds of held-out submissions' native user and system CPU time. An independent 120-second watchdog applies to each run. The diagnostic deadline predictions use terminal counters and fixed whole-millisecond limits. Equality passes. [Collector](../experiments/contest_submissions/bench.py) · [Independent replay](../experiments/contest_submissions/verify.py)

## Results

The instruction/REP clock scaled by the reference solution's time placed seven of eight new-submission inputs within 20% of their native median on each boot. The frozen instruction/REP clock placed one of eight within 20%. The cohort contains two original solutions and two held-out implementations. Rows are inputs, so the eight error measurements do not represent eight independently written submissions.

Each pair below gives boot 1 followed by boot 2. The 70 distinct input/deadline pairs are the same on both guests. Native runs agreed on the verdict for 66 pairs on boot 1 and 67 on boot 2. False timeouts count only limits where all nine native executions passed. [Boot 1 scores and votes](../experiments/results/gce-contest-submissions/metrics.json) · [Boot 2 scores and votes](../experiments/results/gce-contest-submissions-repeat/metrics.json)

| Clock | Median absolute error | Worst absolute error | Within ±20%, of 8 | Majority agreement, of 70 | Unanimous agreement | False timeouts |
|---|---:|---:|---:|---:|---:|---:|
| Frozen instructions + REP | 124.32% / 122.38% | 177.99% / 176.36% | 1 / 1 | 50 / 50 | 47/66, 48/67 | 19 / 19 |
| **Reference-scaled instructions + REP** | **8.00% / 7.00%** | **31.07% / 36.05%** | **7 / 7** | **67 / 67** | **64/66, 65/67** | **2 / 2** |
| Frozen latency + operand bytes | 4010.02% / 3983.84% | 4590.79% / 4529.21% | 0 / 0 | 30 / 30 | 29/66, 29/67 | 37 / 38 |
| Reference-scaled latency + operand bytes | 8.96% / 8.96% | 65.89% / 72.20% | 7 / 7 | 66 / 66 | 63/66, 64/67 | 3 / 3 |
| Reference time alone, retrospective | 27.15% / 30.21% | 40.50% / 40.29% | 1 / 2 | 61 / 61 | 58/66, 59/67 | 5 / 5 |

The retrospective control assigns the original solution's median time directly to the alternate, which requires zero profiling of the alternate. Its three false passes and five false timeouts on unanimous limits per boot show what the work ratio added. All four registered clocks had zero false passes on unanimous limits. On the mixed 150 ms bookmarked-chapters limit, the instruction/REP transfer predicted a pass while seven of nine native runs timed out on boot 1 and eight of nine timed out on boot 2. Rice Hub's four inputs had median absolute errors of 3.13% and 3.65% for the reference-scaled instruction/REP clock. Scrivener's four had 13.59% and 13.91%.

### The same two false timeouts on both boots

On Scrivener's interwoven pages, switching to binary lifting raised the instruction count by 5.3%, modeled latency by 19.4%, and operand-byte count by 33.5%. Yet the new executable finished faster. The reference/native submission medians were 178.291/143.217 ms and 180.321/139.540 ms. The instruction/REP clock projected 187.711 and 189.848 ms. At both 150 and 175 ms it predicted a timeout on each guest, while all nine native submissions passed each limit. All four raw counters were identical across boots on all 16 executable/input pairs. [First guest observations](../experiments/results/gce-contest-submissions/output.tar.gz) · [Second guest observations](../experiments/results/gce-contest-submissions-repeat/output.tar.gz)

Even if we freely choose nonnegative weights for instructions and REP bytes and choose the most favorable of the three original native calibration times, the minimum possible prediction is 186.405 ms on boot 1 and 186.027 ms on boot 2. The slowest observed held-out executions took 148.829 ms and 146.796 ms. These ranges imply minimum errors of 25.25% and 26.72% for that work-ratio formula. For latency and operand bytes the corresponding lower bounds are 42.00% and 43.67%. Each bound uses the fact that a nonnegative weighted work ratio lies between the individual feature ratios when the reference features are positive. This retrospective diagnostic identifies an observed change in the relationship between counted work and native time. The experiment collected no hardware attribution for the speedup. [Bound calculation](../experiments/contest_submissions/bounds.py)

The large absolute errors of the frozen latency/byte clock also have a scale explanation. Its published C++ fit assigned about 1.57 ns per nominal operand byte. The calibration inputs topped out at 50.78 million such bytes. Every alternate in this cohort exceeded that maximum, with 88.69 million to 2.23 billion nominal bytes. Reference scaling reduced this extrapolation error, but the reference-scaled models still missed the speedup on interwoven pages. [Training ranges and original evaluation](https://github.com/seo-rii/aonohako/tree/b938afaa6c171a8a56e24d376d6662f93fcea4c6/docs/durante/gce-v11-cpp20-accuracy)

### Cost and grading scope

Each boot completed 16 diagnostic profiles, 24 reference-native calibrations and 72 independent held-out native observations with zero CPU steal ticks. Eight submission profiles consumed 19.702 and 19.671 seconds of CPU. The median ratio of one full diagnostic profile to its matching native submission median was 106.3× and 105.3×. Profiling the original solutions consumed another 16.584 and 16.576 CPU seconds, and their three native rounds used 1.038 and 1.052 CPU seconds. These measurements used both optional diagnostic counters. Their cost does not measure the ordinary instruction-only client. The replay compared predictions at fixed limits after completed profiles. Aonohako's ordinary grading continues to use its existing native CPU limits. Both temporary guests, boot disks and private SSH keys were removed. [First collection and cleanup](../experiments/results/gce-contest-submissions/collection.json) · [Second collection and cleanup](../experiments/results/gce-contest-submissions-repeat/collection.json)

## Reproduce

Run these commands from a Durante checkout with Python 3.11 or later. The replay checks the original v11 per-process JSONL, ELF and input identities, native CPU accounting, and the kernel's exec order and CPU placement against the registered schedule.

```sh
tmp=$(mktemp -d)
for suffix in '' '-repeat'; do
    data="experiments/results/gce-contest-submissions${suffix}"
    dest="$tmp/boot${suffix}"
    mkdir "$dest"
    tar xzf "$data/output.tar.gz" -C "$dest"
    python3 -B experiments/contest_submissions/verify.py "$dest/output" --output "$dest/metrics.json"
    cmp "$data/metrics.json" "$dest/metrics.json"
done
python3 -B -m unittest discover -s tests -p 'test_contest_submission*.py' -v
```

To rebuild the guest, use the frozen v11 runtime directory from the guarded-sampler study and a toolchain that reproduces the four registered static ELF hashes. The builder checks the binaries and writes the guest's checksums. On a disposable Debian guest with the same CPU topology, run `sudo taskset -c 0 ./guest.sh` from the resulting package. Its systemd unit pins the children to CPU 1. [Runtime preparation](GCE_GUARDED_SAMPLER.md)

```sh
python3 -B experiments/calibrated_limits/prepare.py "$tmp/guest" \
    --runtime "$runtime" --study experiments/contest_submissions \
    --source-dir experiments/contest_accuracy --source-dir experiments/contest_warm
```

# Exact-input calibration on larger contest workloads

Six fresh inputs extend the interleaved timing experiment to longer runs of the same three contest executables. Two known inputs, Wall's crossed intervals and Scrivener's prefix forks, repeat prior near-deadline cases. All eight run through the ordinary text-input driver and have independently computed expected output. The study fixed input and ELF hashes, whole-millisecond deadlines and both guest runs before timing. [Prior short-input results](GCE_CONTEST_WARM_CLOCK.md) · [Registration](../experiments/contest_scale/plan.json) · [Protocol](../experiments/contest_scale/rules.json)

| Program | Two new inputs | Independent answer check |
|---|---|---|
| IOI 2014 Wall | 324,000 range updates on 193,000 columns. One input applies a full raise phase followed by a clamp phase. The other interleaves short updates across the whole wall. | Sweep active extrema to combine the two phases, or apply the short updates directly to the columns. |
| IOI 2012 Crayfish Scrivener | 960,000 interwoven appends and distant reads, or 953,335 operations revisiting bookmarked branches. | Compute each character from its position in the known prefix or branch. Small tests keep copies of complete strings. |
| ICPC World Finals 2017 Money for Nothing | 400,000 suppliers and 400,000 buyers per input. Terraced bids create a dense set of undominated offers. Warehouse lots combine many dominated offers at each site. | Check nearby lattice points for the quadratic maximum on the terraced bids, or enumerate the undominated warehouse representatives. Small cases enumerate all buyer/seller pairs. |

Both temporary GCE guests use AMD Milan, one thread per core, and pin each workload to CPU 1. Both receive the same payload, including the v11 instrumentation used in the earlier experiment. Every input gets one complete Durante profile and one direct native calibration. Three subsequent passes rotate through all inputs. For each input the baseline uses the first native observation and the candidate uses the median of the three later observations. Each sets a Durante work clock, enforced in a fresh execution at each distinct limit with a separate 120-second watchdog. Only then does the guest collect eleven independent native CPU-time references per input. The primary endpoint is median absolute relative error over all six new inputs, assessed separately on each boot. [Collector](../experiments/deadline_accuracy/bench.py)

## Reading the calibration window

On a successful gate selection with `--batch-manifest`, `gate.json` includes the minimum and maximum of the three interleaved native CPU measurements in nanoseconds. The boolean `calibration_window.split` is true when those three runs included both a pass and a timeout at the requested limit. Equality with the limit passes. For example, 98, 99 and 103 ms flag a 100 ms limit even though the median clock predicts a pass. A 103 ms limit gives three passes and `split: false`. The flag costs zero additional executions and the selected median still enforces the deadline. The later, independently sampled native votes let us count how often the flag detects variation at that limit. Three observations can miss it. [Gate implementation](../experiments/deadline_accuracy/gate.py) · [Batch invocation](GCE_CONTEST_WARM_CLOCK.md#prepare-packets-on-a-pinned-worker)

## Results

Each arrow goes from the initial exact-input clock to the interleaved median. An input contributes once to the duration endpoint. Agreement counts use distinct input/deadline pairs and the eleven later direct native votes.

| Prospective inputs | Native median range | Median absolute error | Worst absolute error | Inputs improved / tied / worsened | Agreement on unanimous limits | Agreement by majority | Fixed majority agreement | Extra native CPU |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Boot 1, six | 120.1–177.5 ms | 0.60% → 0.68% | 1.68% → 1.96% | 3 / 0 / 3 | 37/37 → 37/37 | 41/42 → 40/42 | 24/24 → 24/24 | 2.576 s |
| Boot 2, six | 121.9–179.3 ms | 0.58% → 0.46% | 3.06% → 2.36% | 3 / 0 / 3 | 35/36 → 36/36 | 37/41 → 37/41 | 24/24 → 24/24 | 2.557 s |

| Three-sample flag, all distinct limits | Mixed native limits flagged | Unanimous native limits flagged | Candidate majority misses flagged |
|---|---:|---:|---:|
| Boot 1, new inputs | 4/5 | 1/37 | 2/2 |
| Boot 1, previous controls | 1/2 | 0/6 | 1/1 |
| Boot 2, new inputs | 2/5 | 1/36 | 2/4 |
| Boot 2, previous controls | 1/2 | 0/6 | 1/1 |

The reduction in median duration error observed on the shorter cohort did not reproduce on both boots. Each guest had three inputs improve and three worsen. The first-sample clock had median absolute error below 0.60% on both guests. Its worst input had 1.68% error on boot 1 and 3.06% on boot 2. The median signed error of the first and candidate clocks was +0.16% and +0.51% on the first boot and −0.42% and −0.46% on the repeat. These short absolute error margins matter when a whole-millisecond deadline lands close to the runtime. [Boot 1 duration and verdict breakdown](../experiments/results/gce-contest-scale/analysis.json) · [Boot 2 breakdown](../experiments/results/gce-contest-scale-repeat/analysis.json)

On boot 2, Scrivener's interwoven pages measured 173.824 ms on the first payment and 176.044 ms on the interleaved median. Every independent run exceeded the adaptive 174 ms limit (174.832–184.073 ms). The first clock passed and the interleaved clock correctly timed out. Another change on the same boot, at 135 ms on Money's warehouse lots, timed out with only 5/11 native runs exceeding the limit. Both arms therefore had 37/41 overall majority agreement. The six prospective medians fell between 100 and 200 ms on both boots. All 24 prospective fixed deadlines had unanimous native votes and matched both clocks on both guests. The finer adaptive limits exposed differences that the fixed limits missed. [Boot 1 per-limit votes](../experiments/results/gce-contest-scale/metrics.json) · [Boot 2 per-limit votes](../experiments/results/gce-contest-scale-repeat/metrics.json)

Of the five prospective deadlines with mixed native votes on each guest, the calibration flag identified four on the first and two on the second. Each guest also had one flagged deadline with unanimous later votes. On the repeat, two of the four candidate majority misses had no flag. A false flag value therefore conveys agreement only among the three original calibration samples. [Window counts on boot 1](../experiments/results/gce-contest-scale/boundaries.json) · [Window counts on boot 2](../experiments/results/gce-contest-scale-repeat/boundaries.json)

The two earlier controls saw median absolute error fall from 0.67% to 0.55% and from 1.13% to 0.12%. At the same 12 ms Wall deadline, the candidate timed out on both boots while only 2/11 and 5/11 independent native samples exceeded the limit. The three paid samples flagged both decisions. The other five fixed control deadlines had consistent candidate decisions across guests, including Scrivener's 12 ms prefix-forks deadline where the first clock changed from pass to timeout. The prospective and control results apply to exact-input calibration of these trusted static programs. A duration threshold for selecting a default clock would require validation across other inputs, programs and machines.

The traces accounted for 228 and 226 pinned workload PIDs, including 88 independent references per guest. Aonohako's Go reference covered all 100 and 98 live Durante decisions. Each guest used a distinct boot with zero reported steal ticks. The temporary VMs, boot disks and SSH keys were removed. The raw archives retain regular files at their original paths. Identical inputs and outputs are stored consecutively to keep repeated 12.8 MB input records within the compressor's dictionary. [Boot 1 collection](../experiments/results/gce-contest-scale/collection.json) · [Boot 2 collection](../experiments/results/gce-contest-scale-repeat/collection.json) · [Boot 1 Go replay](../experiments/results/gce-contest-scale/aonohako.json) · [Boot 2 Go replay](../experiments/results/gce-contest-scale-repeat/aonohako.json)

## Replay the study

From the Durante checkout, rebuild the registered binaries once with the previously qualified runtime and temporal probe. The collector checks their exact hashes. Set `reference` to the built Aonohako Go reference CLI. The replay checks CPU placement and phase order, registered input/output hashes, every live deadline and each window against its underlying native policies. [Runtime setup](GCE_SHAPE_CALIBRATION.md) · [Aonohako reference](https://github.com/seo-rii/aonohako/pull/155)

```sh
set -eu
tmp=$(mktemp -d)
python3 -B experiments/calibrated_limits/prepare.py "$tmp/guest" \
  --study experiments/contest_scale --runtime "$runtime" --probe "$probe" \
  --source-dir experiments/contest_warm --source-dir experiments/contest_accuracy
for name in gce-contest-scale gce-contest-scale-repeat; do
  data="experiments/results/$name"
  dest="$tmp/$name"
  mkdir "$dest"
  tar xJf "$data/output.tar.xz" -C "$dest"
  python3 -B experiments/deadline_accuracy/verify.py "$dest/output" --strategy interleaved-three --output "$dest/metrics.json"
  python3 -B experiments/contest_warm/analysis.py "$dest/output" --strategy interleaved-three --output "$dest/analysis.json"
  python3 -B experiments/contest_scale/boundaries.py "$dest/output" --output "$dest/boundaries.json"
  python3 -B experiments/deadline_accuracy/replay_aonohako.py "$dest/output" --strategy interleaved-three \
    --binaries "$tmp/guest/binaries" --reference "$reference" --output "$dest/aonohako.json"
  for report in metrics analysis boundaries aonohako; do
    cmp "$dest/$report.json" "$data/$report.json"
  done
done
python3 -B -m unittest discover -s tests -p 'test_contest_scale*.py' -v
```

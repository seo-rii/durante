# Reusing native calibrations across memory footprints

On a CPU-pinned AMD Milan Compute Engine guest, the temporal selector from #85 let five runs finish at deadlines where all nine native references timed out. The footprint selector stopped all five. Four corrections were on previously unseen inputs. The change cost three additional native calibrations across 13 holdouts. [Timing and decisions](../experiments/results/gce-footprint-admission/metrics.json)

Both selectors profiled the same trusted static C++ executable and exact stdin in one run. Each kept an independent bank of paid calibrations for that executable, taking the earliest compatible one. In addition to #85's spatial and temporal checks, the footprint selector compares each load site responsible for at least 5% of eligible loads in either profile. If either sampled address span reaches 1 MiB, the larger span must be at most 1.5 times the smaller span (with a 4 KiB floor). This tests an observed address range. It can include gaps and depends on which addresses the probe samples. [Registered rule](../experiments/footprint_admission/rules.json)

The preregistration fixed three starting inputs, four previously seen holdouts and nine prospective holdouts across Fenwick trees, binary searches and sliding windows. Each of the 52 input/deadline pairs ran once per selector using the selected clock in the live exact-input wrapper. A separate watchdog allowed 120 seconds. After all selections, each holdout ran natively nine times in rotated order. All 248 measured PIDs executed on CPU 1, with one thread per core, zero steal ticks and zero lost trace events. [Plan](../experiments/footprint_admission/plan.json) · [Raw evidence](../experiments/results/gce-footprint-admission/output.tar.xz) · [Guest and cleanup](../experiments/results/gce-footprint-admission/collection.json)

| Holdouts | Estimates within ±20%, temporal → footprint | Live decisions matching unanimous native results | Mixed native deadlines |
|---|---:|---:|---:|
| 4 previously seen | 3/4 → 4/4 | 15/16 → 16/16 | 0 |
| 9 prospective | 7/9 → 9/9 | 30/34 → 34/34 | 2 |
| All 13 | 10/13 → 13/13 | 45/50 → 50/50 | 2 |

For the known eight-MiB Fenwick tree, the old estimate was 170.93 ms and the new one was 217.93 ms, against a native range of 213.93–220.83 ms. Paying for that input also fixed two new cases through reuse, at 150 and 175 ms. A still larger tree needed its own calibration and fixed two deadlines, 175 and 225 ms. Both selectors' complete preflight predictions agreed with every one of their actual enforced outcomes. The worst absolute median error fell from 36.18% to 13.86%.

Native variability still mattered. At 175 ms, seven of nine native runs on the 5.5 MiB tree exceeded the limit while both selected clocks allowed completion. At 60 ms, one of nine runs on the smaller sliding window exceeded the limit while both clocks timed out. These mixed deadlines are excluded from the unanimous accuracy counts. The remaining maximum error occurred on the 5.5 MiB tree. The sampled span ratio is a heuristic calibrated on one processor family and three programs.

On the holdouts, both arms consumed 4.886 s of target CPU for the shared full profiles. Temporal admission paid for five native runs (0.508 s) and footprint admission paid for eight (1.071 s). Total holdout preflight CPU rose from 5.394 s to 5.957 s (+10.4%). The three initial profiles and anchors jointly cost an additional 0.796 s per strategy. These costs exclude the later live enforcement and independent validation runs.

## Reproduce

Extract the archive into a new temporary directory and reconstruct the metrics from the raw observations.

```sh
tmp=$(mktemp -d)
data=experiments/results/gce-footprint-admission
tar -xJf "$data/output.tar.xz" -C "$tmp"
python3 -B experiments/footprint_admission/verify.py "$tmp/output" --output "$tmp/metrics.json"
cmp "$tmp/metrics.json" "$data/metrics.json"
```

For a new trusted run, use the [capture and selection commands](GCE_SHAPE_CALIBRATION.md) with the combined sampler and select `--method footprint`. The [Aonohako projection](../experiments/shape_calibration/aonohako_clock.md) exports bounded integer weights for specified deadlines and binds them to the preflight, policy, ELF and stdin. These measurements support diagnostic use on the tested workloads. The production judging policy remains subject to separate runtime and workload qualification.

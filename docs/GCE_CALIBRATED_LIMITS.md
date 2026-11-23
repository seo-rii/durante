# Enforcing a calibrated Durante clock during execution

On a Compute Engine CPU pinned to the judge process, the diagnostic stopped 15 jobs during execution and allowed 17 to finish. All **32 decisions** matched the time computed from a separate complete metered run on the same input. Nine independent native executions per input agreed with 28 decisions, exceeded two limits the diagnostic allowed, and spanned the other two limits. Both missed timeouts occurred on C++ algorithms outside the previous corpus. [Native times and decisions](../experiments/results/gce-calibrated-limits/metrics.json) · [Raw receipts](../experiments/results/gce-calibrated-limits/output.tar.xz)

| Held-out inputs | Complete predictions within 20% of native median | Limits | Agree with all nine native runs | False timeout | Missed timeout | Native outcomes split |
|---|---:|---:|---:|---:|---:|---:|
| 8 from the five earlier programs | 8/8 | 16 | 14 | 0 | 0 | 2 |
| 4 from two new algorithms | 2/4 | 16 | 14 | 0 | 2 | 0 |

## Running a new input with the work clock

The standalone command uses the first-input policy created by the [per-executable calibration tool](GCE_GUARDED_NATIVE.md#calibrating-another-executable). It accepts a trusted, deterministic, single-process static Linux x86-64 ELF and the registered [v11 runtime payload](GCE_GUARDED_SAMPLER.md#using-the-diagnostic-on-a-new-executable). The target runs once. Its output, held clock, decision and PID-linked evidence are saved under a new directory.

```sh
taskset -c "$cpu" python3 experiments/guarded_native/live.py \
  --policy "$anchor_file" --elf "$elf" --stdin "$next_input" \
  --payload "$payload" --out "$new_run_dir" --limit-ms 100 --timeout-ms 120000
```

The parent weights the published instructions and REP bytes, multiplies by the first native CPU time, divides by the first input's weighted work and rounds up once to nanoseconds. A value strictly above the limit crosses the threshold. A 5 ms polling interval is the default. In these 15 live stops, the largest excess in the **published estimate** at detection was 5.96 ms. It occurred on `rep_fill/4` at a 100 ms limit. The amount depends on publication, polling and the workload. [Clock implementation](../experiments/guarded_native/live.py)

| Exit | Interpretation | Evidence |
|---:|---|---|
| 0 | Completed within budget | Successful exit and terminal v11 counters reconciled to the final clock |
| 123 | Work budget exceeded | A reaped SIGKILL and its crossing snapshot, or an over-budget terminal record if the job finished between polls |
| 124 | Outer wall deadline reached | Separate watchdog status |
| 1 | Clock unavailable | Diagnostic reason in the result when the run directory was created |

Local real-ELF tests also exercised an over-limit natural exit with a deliberately slow polling interval and a separate wall timeout. The GCE runs all used the registered 5 ms interval and 120 s watchdog. This diagnostic uses an application-mutable clock. Contestant sandboxing and grading authority remain separate requirements.

## Where time prediction failed

| New input | Native CPU median (nine-run range) | Calibrated complete time | Effect at the registered limits |
|---|---:|---:|---|
| Fenwick tree, more operations at the original size | 142.17 ms (142.02–143.44) | 143.47 ms | All four agreed |
| Fenwick tree, larger working set | 85.96 ms (81.38–92.19) | 62.98 ms | Missed 75 ms |
| Binary search, more ordered queries | 143.16 ms (143.01–143.31) | 144.26 ms | All four agreed |
| Binary search, shuffled queries | 135.22 ms (135.04–137.53) | 48.14 ms | Missed 75 ms |

The Fenwick program increases the tree from 16,384 to 1,048,576 elements with the same operation count as calibration. The binary-search program changes the query order with the same number of queries and elements. Shuffling increased the measured weighted work by 0.0324% relative to its first input. Native CPU grew by a factor of 2.81. No hardware counters were collected to apportion the added time between branch prediction and memory behavior. The two split outcomes on the earlier corpus were `rep_fill/2` at 67 ms and `rep_fill/4` at 151 ms. [Workloads and registered limits](../experiments/calibrated_limits/plan.json)

A post-hoc calculation shows why repeating the native calibration alone cannot fix the two lookup inputs. The complete runs performed 733,788,545 and 244,871,908 instructions with 632 REP bytes each. Their weighted-work ratio was 2.997. Even the fastest shuffled run and the slowest ordered long run give a native ratio of only 1.061. For the observed counts, any common nonnegative scale applied to these frozen weights must miss at least one input by **47.69%**, allowing for integer rounding. This bound uses both observed native ranges and permits a different choice of first calibration. [Exact rational calculation](../experiments/results/gce-calibrated-limits/metrics.json)

## Reproducing the measurement

The registered thresholds preceded collection. The earlier eight inputs used the relative limits fixed by the previous experiment. Each new input ran at 25, 75, 150 and 300 ms. A separate 60 s work limit provided one complete control per input. Each of seven executables received one initial full metered capture and one direct native calibration. The twelve held-out inputs each received nine rotated native runs and one run per declared limit. We retained reports even when the prediction missed the native deadline. [Registration](https://github.com/seo-rii/durante/commit/3990f1a) · [Guest collector](../experiments/calibrated_limits/bench.py)

The guest used AMD Milan, n2d-standard-4 with one thread per core. Cgroup placement and process affinity restricted the collector and targets to CPU 1. The trace controller ran on CPU 0. The kernel trace covers 166 relevant process IDs. CPU steal and trace loss were zero. The VM and boot disk returned HTTP 404 after deletion, and the temporary SSH key was removed. [Guest and cleanup receipt](../experiments/results/gce-calibrated-limits/collection.json)

```sh
study=experiments/calibrated_limits
data=experiments/results/gce-calibrated-limits
scratch=$(mktemp -d)
tar -xJf "$data/output.tar.xz" -C "$scratch"
python3 "$study/verify.py" "$scratch/output" --output "$scratch/metrics.json"
cmp "$scratch/metrics.json" "$data/metrics.json"
python3 -m unittest discover -s tests -p 'test_calibrated_limits.py'
```

To rebuild the guest payload, use `python3 "$study/prepare.py" "$scratch/guest" --runtime "$payload"` with the registered #82 runtime and Debian g++ 12.2.0-14+deb12u1. Preparation checks each static ELF against its registered SHA-256. Run the payload's `guest.sh` as root pinned to CPU 0 on a disposable two-core guest.

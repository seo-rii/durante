# GCD operand dependencies and loop lengths on a pinned Compute Engine CPU

On a pinned AMD Milan guest, the same Euclidean divisions took **38.507 ms or 20.102 ms** depending on whether the closing branch used the new remainder or an independent countdown. After sorting input pairs by trip count, the same one-byte change had little effect. Durante's divider clock estimated **30.951 ms in all four cases**. [Timing observations](../experiments/results/gce-gcd-controls/dataset.json.gz) · [Predictions and paired contrasts](../experiments/results/gce-gcd-controls/evaluation.json)

## Native CPU time and predicted time

| Condition | Native median | v9001 estimate | With the registered online length-change correction |
| --- | ---: | ---: | ---: |
| Original Rust GCD | 39.291 ms | 30.392 ms | 39.277 ms |
| Replayed operands, independent | 30.258 ms | 18.645 ms | 18.645 ms |
| Previous remainder feeds dividend | 47.804 ms | 33.859 ms | 33.859 ms |
| Previous remainder feeds divisor | 47.835 ms | 33.859 ms | 33.859 ms |
| Remainder branch, original order | 38.507 ms | 30.951 ms | 39.835 ms |
| Remainder branch, grouped order | 19.711 ms | 30.951 ms | 30.951 ms |
| Countdown branch, original order | 20.102 ms | 30.951 ms | 39.835 ms |
| Countdown branch, grouped order | 19.908 ms | 30.951 ms | 30.951 ms |
| Rust grouped floor quotients | 52.127 ms | 46.595 ms | 46.601 ms |
| Rust batched quotients | 64.775 ms | 74.239 ms | 74.24 ms |

Grouping saved 18.796 ms when the branch waited on the computed remainder and 0.195 ms when it used the independent countdown. The difference between those order effects was 18.602 ms, ranging from 18.256 to 18.780 ms over the four rounds. At fixed original order, the two variants had identical divider cycles, nominal operand bytes, instructions, loop visits, completions and consecutive-length changes. Even using the closest two native endpoints, any shared estimate has at least **30.83% relative error** on one of the pair. Three of the four assembly loop conditions had at least one native duration under the registered 20 ms minimum. Every workload had less than 2.54% native spread; all observations are retained. [Per-case ranges and error bounds](../experiments/results/gce-gcd-controls/evaluation.json)

Serializing the operand replay through either input added about 17.56 ms compared with the independent stream. Divisor minus dividend was only 0.031 ms, with observed endpoint differences from −0.711 to +0.670 ms. A surcharge specifically on the divisor path predicted a 9.401 ms difference. It brought the divisor case inside ±20% while leaving the same-sized dividend error. The raw contrasts support a cost for the dependency through either operand and provide no evidence for this divisor-specific surcharge. [All seven registered contrasts](../experiments/results/gce-gcd-controls/evaluation.json)

The online length-change model passed its Rust calibration anchor, one of the four loop controls, zero of the three operand replays and both previously measured transfer workloads. The probe reproduced the input-derived counts for every loop and stream in all four rounds. Its pooled CPU overhead was **1.05%** (24.437 s vs 24.182 s across 40 pairs), 3.85% on the four loop controls and 2.46% on the Rust GCD. The median overhead across individual pairs was 1.92%. All 28 assembly pairs had identical canonical counters. Independent Rust launches differed by up to 1.61 µs in their modeled baseline, so online corrections use the counters from the same instrumented run. [Per-case counts, CPU times and estimates](../experiments/results/gce-gcd-controls/evaluation.json)

## What the controls change

The archived Rust program computes 524,288 Euclidean GCDs and executes 9,525,462 unsigned 32-bit divisions. An earlier run on an AMD Milan guest took 39.276 ms of native CPU. Durante's version-9001 divider clock estimated 30.392 ms. We used that 8.884 ms residual to freeze three competing predictions before collecting new timings. The original Rust ELF serves as the calibration anchor. The seven assembly controls test the predictions, and two other Rust programs test how they transfer. [Previous divider study](GCE_DIVIDE_CLOCK.md) · [Registration commit](https://github.com/seo-rii/durante/commit/6a967b3e2ee15ea6589bbdab4db486c6731efa79) · [Evaluation plan](../experiments/gcd_controls/evaluation-plan.json)

| Control | Held constant | Changed |
| --- | --- | --- |
| Three operand replays | Same 114,305,544-byte input, dividend and divisor sequence, quotient/remainder values, checksum and instruction sequence | One byte selects whether a zero derived from the previous actual remainder feeds the next dividend, the divisor or a scratch register |
| Two orders × two loop conditions | Same 524,288 input pairs, true Euclidean recurrence, number of divisions and 16-byte result | Reorder pairs by trip count, or change one instruction byte to branch on the precomputed countdown instead of the new remainder |
| Three Rust programs | Original executable and original input from the previous study | Fresh native CPU measurements and a separate run with the loop probe enabled |

The operand replay computes the zero as the XOR of the previous measured remainder and the expected remainder in the input. This preserves the numerical operands while introducing a real machine dependency into the selected input. Its time includes the XOR, addition and table access. In the loop controls, grouping reduces the number of changes in consecutive trip counts from 480,087 to 30. Changing which value supplies the branch flags preserves its outcome sequence for a given input order. Grouping also changes the order in which numerical operands reach the divider. [Operand replay assembly and generator](../experiments/gcd_controls/stream) · [Loop control assembly and generator](../experiments/gcd_controls/loops)

We assigned the historical residual independently to each candidate feature. The implied cost is 0.987 ns per remainder-to-divisor edge, 16.945 ns per completed chain or 18.505 ns per change in consecutive chain lengths. The new probe tests the latter two rules online with the same frozen coefficients. It counts visits and completed traversals at eligible backward branches surrounding an unsigned 32-bit divide in the main ELF. Changes are counted at the same branch site and exclude the first completion. These are measured software events. The experiment has no hardware branch-misprediction or divider-stall counts. [Exact rates and population](../experiments/gcd_controls/calibration.json) · [Probe and hand-counted fixture](../experiments/gcd_controls/trip_probe)

## Measurement and reproduction

Each of four rounds ran every case through four processes on guest CPU 1. Native runs bracketed a version-9001 run and a version-9001 run with the loop probe. The registration counterbalanced the order of the two instrumented runs and rotated or reversed program order across rounds. Native time is whole-child user plus system CPU from `wait4`, including startup, mappings and first touches. The collector pre-read all registered input files and reopened the same file read-only for each process. Guest CPU 0 hosted the placement observer. [Collection protocol](../experiments/gcd_controls/protocol.json) · [Collector](../experiments/gcd_controls/collect.py)

The scheduler trace places all 160 measured processes and 80 same-PID loader executions on CPU 1. The observer polled 210 times on CPU 0. There were zero trace overruns and zero guest steal ticks during the 52-second acquisition. The VM and boot disk both returned HTTP 404 after deletion. [Placement evidence and cleanup](../experiments/results/gce-gcd-controls/platform.json) · [Original wait4 and meter records](../experiments/results/gce-gcd-controls/receipts.tar.xz)

The regression test checks the fixture generators, raw per-process outputs and meter records, scheduler placement, scores and measured loop counts against their input oracles. To replay from the repository root on Linux x86-64, run:

```sh
python3 -m unittest discover -s tests -p 'test_gcd_controls.py'
```

To regenerate the five regular files used as stdin, pass a fresh output directory to the input generator. The compressed dataset and the receipt archive contain the observations and original per-process records. [Input generator](../experiments/gcd_controls/inputs.py) · [Raw records and scores](../experiments/results/gce-gcd-controls)

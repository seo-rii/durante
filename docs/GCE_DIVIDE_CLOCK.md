# Unsigned division timing on a pinned Compute Engine CPU

**The translation-time divider model brought both new Rust programs inside the ±20% timing target. It still underestimated GCD by 22.62%.** Computing the correction when translating a block took 22.41% less total instrumented CPU than recording block entries for offline replay. Its total CPU was 0.28% above the existing meter on this four-program workload mix. Aonohako's general Rust verdict clock remains unqualified. [Prospective evaluation](../experiments/results/divide-clock/fresh/evaluation.json)

## Where the GCD estimate comes from

The archived Rust executable computes 524,288 Euclidean GCDs. Its hot instruction performs unsigned 32-bit division 9,525,462 times. The quotient is at most three for 69.51% of the divisions. Each iteration uses the previous remainder to form the next pair of operands. A source-level replay reproduced the final checksum of 5,324,962 and the exact count at the machine instruction. The divides were split between 9,001,174 entries into a basic block that branches back to itself and 524,288 first visits in other blocks. The old model charges 21 cycles per entry to the self-loop. Its whole-process prediction of about 62 ms was 57.65% above the 39.35 ms historical native median. [Operand replay](../experiments/divide_clock/check_operands.py) · [Exact operands and counts](../experiments/results/divide-clock/operand-profiles.json)

The earlier assembly controls all execute 33,554,432 unsigned 32-bit divisions by 251. They cover 9-bit and 31-bit dividend ranges, one or four independent remainder chains, and two ways to zero the high dividend register. The small dividends yield quotients of one or two. The large dividends yield 23-bit quotients. The Rust GCD uses 251 as its divisor only 1,825 times. This difference matters when evaluating how well a constant-cost model transfers. [Original measured controls](GCE_DEPENDENCY_PAIRS.md)

## A static cost per basic block

The candidate keeps the earlier Rust clock's coefficients for modeled cycles and nominal memory operand bytes. It changes the cycle count only for blocks in the main executable containing unsigned 32-bit DIV. It removes the input dependency of same-register 32/64-bit XOR, estimates a dependent divide with latency `L`, and charges at least `I × eligible divides` cycles per block for divider occupancy. It takes the maximum of that floor, the existing four-wide instruction issue floor and the estimated loop-carried dependency. The recurrence applies to basic blocks with a conditional back edge to their own start. Loads, signed division, other divide widths and shared libraries retain the corresponding v1 rules. The parameters describe this estimator on this corpus. They do not directly measure hardware instruction latency or throughput. [Parameter definitions and fit](../experiments/divide_clock/protocol.json)

The first client records block descriptors and counts entries for offline replay. The second computes the fitted cost when DynamoRIO translates the block. Execution then uses the ordinary Durante block-entry accounting code. This moves the calculation out of the hot path without collecting divide operands on every execution. The experimental stream declares latency version 9001. The normal reader rejects this version. The study reader validates the original marker, replays the full receipt checks and labels the returned metric as 9001. [Descriptor client](../experiments/divide_clock/probe.c) · [Translation-time client](../experiments/divide_clock/fast/fast.c) · [Study reader](../experiments/divide_clock/fast_reader.py)

## What transferred from the original controls

The four earlier controls on the first GCE VM were the only training data. Minimizing squared relative errors selected **9 cycles of latency and a 6-cycle issue interval**. A separate fit with latency alone selected 11 cycles. Both fits and the prospective workload identities were published before timing the fresh programs. A case passes when every prediction lies within ±20% of the median native process CPU time. The historical workloads and their native reference times were known from prior studies. [Frozen parameters](../experiments/divide_clock/parameters.json) · [Acquisition freeze](https://github.com/seo-rii/durante/commit/a348a25883e68a297c46b8caa58d6b2f570bc736)

| Model | First-VM training controls | Second-VM assembly controls | Eight Rust programs | Rust GCD error |
| --- | ---: | ---: | ---: | ---: |
| Existing clock | 0/4 | 1/4 | 5/8 | +57.65% |
| Correct the XOR dependency | 2/4 | 2/4 | 5/8 | +57.65% |
| Fit latency, correct XOR | 1/4 | 2/4 | 6/8 | −8.12% |
| Fit latency and issue interval, correct XOR | 4/4 | 4/4 | 5/8 | −22.74% |

For each historical workload, the native reference is the archived GCE `wait4` data. The new block descriptors were collected locally in two diagnostic runs using the original ELF, input, patched DynamoRIO runtime and baseline client. Block shapes and entry counts matched exactly across both local runs for all 16 programs. For all eight assembly executables, baseline production counters matched between the local replay and the historical GCE runs. For Rust the largest local vs historical GCE baseline time-estimate difference was 0.043%. The historical results are therefore cross-host diagnostic replays. The fresh comparison below measures all three modes directly on one pinned GCE core. [Archived predictions](../experiments/results/divide-clock/evaluation.json) · [Local vs GCE comparison](../experiments/results/divide-clock/coverage.json)

After the fit, we checked all 246 registered integer parameter pairs against the eight assembly controls and GCD together. The best worst error against their native medians was 20.055%, narrowly outside the gate. Favorable choices inside the native and diagnostic ranges admitted three pairs, so this particular miss is sensitive to timing variation. Binary search presents a stronger limitation. All 246 choices put its estimate between 21.67 and 23.05 ms, compared with its 34.45 ms native median. Even the closest range endpoints leave at least 31.79% error. Six of the eight Rust programs executed no eligible divides. [Parameter sensitivity](../experiments/results/divide-clock/feasibility.json) · [Previous memory-order experiment](GCE_ACCESS_SAMPLE.md)

## Prospective comparison on a new pinned GCE guest

The two previously unmeasured Rust programs exercise different forms of division. One groups consecutive integers with equal floor quotient to sum the divisor counts up to each input. The next group depends on both quotients in the current iteration. The other sums independent pairwise quotients over a 4,096-entry array. The workloads executed 14,572,040 and 33,554,432 eligible machine divisions respectively, matching the counts derived independently from their inputs. Their checksums, compiled ELFs and the clock parameters were fixed before collecting native timings. The archived serial control and Rust GCD also ran on this guest. [Programs](../experiments/divide_clock/fresh/holdouts.rs) · [Independent checksums and fixture identities](../experiments/results/divide-clock/fresh/fixtures.json)

| Program | Native median | Ordinary clock | Fast clock | Signed error, ordinary → fast |
| --- | ---: | ---: | ---: | ---: |
| New grouped floor quotients | 51.915 ms | 104.78 ms | 46.59 ms | +101.82% → −10.25% |
| New batched quotients | 64.724 ms | 31.37 ms | 74.24 ms | −51.54% → +14.70% |
| Original Rust GCD | 39.276 ms | 62.02 ms | 30.39 ms | +57.91% → −22.62% |
| Original 31-bit serial control | 136.584 ms | 246.52 ms | 128.62 ms | +80.49% → −5.83% |

Each row compares the median of six native executions with the median of three estimates from the indicated meter. Each program's six native observations had less than 1.60% spread. GCD ranged from 39.213 to 39.841 ms, well above its 30.39 ms estimate. All three fast estimates for each new workload were within the target. The fast client's cycle total agreed with offline replay of the fitted model within 41 ppm on every paired run, including exact agreement for the freestanding serial control. Independent process startup is included in both sides of the comparison. [Dataset](../experiments/results/divide-clock/fresh/dataset.json.gz) · [Original stdout, wait4 receipts and counter streams](../experiments/results/divide-clock/fresh/receipts.tar.xz)

Across 12 matched comparisons the ordinary meter consumed **12.335 s** of child CPU, the descriptor-recording client plus ordinary meter consumed **15.941 s**, and the fast meter consumed **12.369 s**. Relative to the ordinary meter the pooled increases were 29.23% and 0.28% respectively. The median paired increase for the fast meter was 0.05%. The aggregate is weighted by instrumented runtime, of which batched quotients is the largest contributor. These differences include process and client startup. They describe diagnostic CPU cost on this guest and workload mix. [Per-case CPU totals and paired ratios](../experiments/results/divide-clock/fresh/evaluation.json)

Each round rotated the four cases. Two native processes bracketed the ordinary, descriptor-recording and fast diagnostic runs. Within each case each diagnostic occupied each slot once. On a Seoul AMD EPYC 7B13 guest, scheduler evidence places all 60 measured processes and all 36 same-PID loader executions on CPU 1. The CPU 0 observer polled 171 times in 43 seconds. The trace reported zero dropped events and CPU 1 accumulated zero guest steal ticks. After export, both the temporary VM and its boot disk returned HTTP 404. [Acquisition protocol](../experiments/divide_clock/fresh/protocol.json) · [Platform and cleanup evidence](../experiments/results/divide-clock/fresh/platform.json)

The subsequent GCD experiment replays the same numerical operands and compares the native cost of changing their dependency path or the order of Euclidean loop lengths. [Matched GCD controls and online probe](GCE_GCD_CONTROLS.md)

## Reproduction

Run from the repository root. The historical verifier reads the previously published native receipts. The fresh archive includes the new Rust ELFs and records the paths to the preserved control ELFs. The test compares the distinct clients only in their registered single-threaded, uninterrupted scope.

```sh
study=experiments/divide_clock
result=experiments/results/divide-clock
python3 "$study/collect.py" verify "$result/observations.json.gz" "$result/receipts.tar.xz"
python3 "$study/check_operands.py" --verify "$result/operand-profiles.json"
python3 "$study/coverage.py" "$result/observations.json.gz" "$result/receipts.tar.xz" --verify "$result/coverage.json"
python3 "$study/score.py" --verify
python3 "$study/feasibility.py" --verify
python3 "$study/fresh/fixtures.py" --verify
python3 "$study/fresh/verify.py" "$result/fresh/dataset.json.gz" "$result/fresh/receipts.tar.xz"
python3 "$study/fresh/placement.py" "$result/fresh/dataset.json.gz" "$result/fresh/placement.json.gz" --platform "$result/fresh/platform.json"
python3 "$study/fresh/score_fresh.py" --verify
```

To rebuild the two research clients, use a Linux x86-64 host and set `DynamoRIO_DIR` to the DynamoRIO 11.3.0-1 SDK's `cmake` directory. The live check exercises the four integer widths, signed and unsigned encodings, and the production reader's rejection of the experimental version.

```sh
cmake -S "$study" -B /tmp/divide-probe -DCMAKE_BUILD_TYPE=Release -DDynamoRIO_DIR="$DynamoRIO_DIR"
cmake --build /tmp/divide-probe
cmake -S "$study/fast" -B /tmp/divide-fast -DCMAKE_BUILD_TYPE=Release -DDynamoRIO_DIR="$DynamoRIO_DIR"
cmake --build /tmp/divide-fast
python3 "$study/check_fast.py" --drrun "$BUNDLE/dynamorio/bin64/drrun" --meter "$METER" \
  --probe /tmp/divide-probe/libdivide_clock.so --fast /tmp/divide-fast/libdivide_clock_fast.so
```

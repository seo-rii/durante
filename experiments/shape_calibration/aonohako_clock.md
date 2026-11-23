# Per-input deadline projection for Aonohako

The offline exporter converts a trusted Durante calibration to the three positive
uint64 coefficients used by Aonohako's `weighted-work-nanoseconds-v1` clock. It
certifies the strict deadline decision for each requested limit. The receipt
applies to the selected static x86-64 ELF and exact stdin and carries diagnostic
authority. The nanosecond estimate between certified deadlines can differ from
Durante's exact rational estimate.

Pass the standalone selection receipt, current capture, paid single-CPU
calibration and original input to the exporter. It rechecks the capture and
selection using the same checks as the selected-input live launcher. This
command operates on existing files without running the executable.

```sh
python3 experiments/shape_calibration/aonohako_clock.py \
  --selection selection.json --run current-capture --policy anchor/policy.json \
  --elf ./trusted-static-program --stdin case.in \
  --limit-ms 25 --limit-ms 75 --limit-ms 150 --limit-ms 300 --out projection.json
```

The receipt records the hashes of the ELF, input, selection, current result and
sample, calibration, paid policy and frozen reference. Its `weights` object is
a coefficient fragment. A diagnostic comparison must bind the receipt to its
trusted preflight and inputs, use one listed millisecond limit and validate the
counter snapshot. Runtime-wide registration would discard its per-input scope.

## Strict comparison and verification

Let `a/c` and `b/c` be the exact instruction and combined REP-byte coefficients
after scaling the frozen reference by the anchor's native CPU time. For an
integer deadline `T` in nanoseconds, the within-budget set consists of the
nonnegative integer pairs `(i,r)` satisfying `a*i + b*r <= c*T`. The sum
`r = MOVS bytes + STOS bytes` covers the two separate work-clock counters.

When the exact coefficients fit, the exporter keeps them. Otherwise it tries
up to 131,072 denominators near the largest representable denominator. It
rounds both coefficients down or both up and tests the best 32 candidates
from each direction against 1–16 requested deadlines. Rounding in the same
direction nests the two within-budget sets. Their cardinalities are the sum of
one-dimensional floor divisions over successive instruction counts. Euclidean
division evaluates this sum without enumerating the instructions or bytes.
Equal counts prove that every pair receives the same decision, including
equality at the deadline. Failure to find a certificate results in a nonzero
exit with no new receipt. Another denominator could still admit a certificate.

The #85 replay exported all ten temporal holdout selections across three
ELFs and six paid calibrations at 25, 75, 150 and 300 ms. Aonohako commit
`7e2831f1afd38e7479d092bb8cb5cdf774f1416b` evaluated its
`WorkClockRequest.Exceeds` method on the recorded instruction, MOVS and STOS
counts. All 40 results matched the recorded decisions, with 20 within budget
and 20 over budget. This replay uses valid snapshots and measures agreement
with the calibrated clock. Native CPU-time accuracy is evaluated separately
by the corpus benchmark.

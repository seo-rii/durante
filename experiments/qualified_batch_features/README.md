# Time estimates for held-out batch judge inputs

In the Aonohako scoped-judge pilot, a live instruction-plus-REP clock agreed
with ordinary judging at all three wide contest budgets. It disagreed on two
of six reference deadlines on both validation guests; its largest completed
held-out time error was 67.72%. This experiment checks whether the v12
terminal latency-bound and memory-operand counters contain information
missing from the live clock. [Pilot and underlying receipts](https://github.com/seo-rii/aonohako/tree/0f11b8c17ff7e1b11072649a1145853f338e6253/docs/durante/gce-qualified-judge)

## Measurements

The reference-only through-origin fit increased the number of new input and
solver pairs within ±20% from 6 to 8 of 12. At thresholds set to 120% of
the native median, false-timeout estimates dropped from 12 to 8 of 24
counter readings. The largest error under this fit was 67.87%. Latency
with nominal operand bytes hit the same eight cases but had a 187% outlier.
Both two-feature fits assigned zero weight to operand bytes.

| Candidate | Both readings within ±20% | Median absolute error | Worst absolute error |
|---|---:|---:|---:|
| Frozen shared live clock | 6/12 | 20.46% | 78.67% |
| Instruction + REP, absolute-time fit | 8/12 | 15.05% | 67.87% |
| Latency + operand bytes | 8/12 | 16.13% | 187.00% |
| Instructions + operand bytes | 6/12 | 17.55% | 74.36% |
| Latency alone | 8/12 | 16.13% | 187.00% |

The new live-compatible coefficient is 1,139,371,945,900,000 /
4,752,214,584,908,649 nanoseconds per instruction or REP byte. The guest
measured 75 ordinary executions and 30 diagnostic executions. Across the
12 held-out groups the median instrumented-to-native CPU ratio was 43.05
with both diagnostic counters enabled. This ratio includes all instrumentation
overhead. The measurements do not isolate the incremental cost of either
counter. [Case-level estimates](results.json) · [Original receipts](evidence/trials.jsonl.gz) ·
[Platform and deletion evidence](evidence/infrastructure.json)

To reproduce the fit and scores from this checkout, run the following.
Add `--payload` with the prepared payload path to verify full HTTP request
digests against the original binary bytes.

```sh
cd experiments/qualified_batch_features
python3 -B analysis.py --evidence evidence --infrastructure evidence/infrastructure.json
python3 -B -m unittest test_analysis.py
```

## Registered comparison

One fresh SMT-disabled Compute Engine AMD Milan guest runs the exact pilot
worker on CPU 1. Ordinary Aonohako runs return CPU times. Separate runs of
the same executable return terminal Durante counters. Each input/executable
pair gets five native runs and two diagnostic runs; kernel exec records
check placement. Only the three original reference programs and inputs are
used for training. Evaluation uses six new seeded inputs, each with a fast
correct implementation and an incorrect implementation. The held-out
programs also appeared on other inputs in the earlier pilot.

The baseline uses the previously frozen shared work coefficient. A second
model fits the same work count to the three new reference medians by least
squares through the origin. Three further candidates minimize squared
relative time error on those reference medians, with nonnegative coefficients
and zero intercept. They use latency-bound cycles scaled by 16 plus nominal
memory-operand bytes, instructions plus nominal memory-operand bytes, and
latency bound alone. The fit precedes held-out scoring and uses exact
rational arithmetic. Every model is reported. In the ±20% test both counter
readings must predict the median of five native CPU readings within the
threshold. The 80% and 120% deadline comparisons are offline decisions.

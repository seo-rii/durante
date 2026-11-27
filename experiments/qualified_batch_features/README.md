# Time estimates for held-out batch judge inputs

In the Aonohako scoped-judge pilot, a live instruction-plus-REP clock agreed
with ordinary judging at all three wide contest budgets. It disagreed on two
of six reference deadlines on both validation guests; its largest completed
held-out time error was 67.72%. This experiment checks whether the v12
terminal latency-bound and memory-operand counters contain information
missing from the live clock. [Pilot and underlying receipts](https://github.com/seo-rii/aonohako/tree/0f11b8c17ff7e1b11072649a1145853f338e6253/docs/durante/gce-qualified-judge)

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

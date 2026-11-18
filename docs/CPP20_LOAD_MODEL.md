# Does counting load instructions improve CPP20 timing predictions?

In the branch/sort replay, the total number of instructions that read memory
distinguished both pairs of programs. This experiment tests whether that count
also improves predictions on two further algorithms. [Earlier replay](REFERENCE_FEATURES.md)

## Study design

The baseline predicts native CPU nanoseconds from instructions and REP bytes.
The candidate adds one nonnegative coefficient for load instructions. Both
fits have zero intercept. Each of nine training cases contributes one row
using its median counters and the median of six native runs. The objective is
the sum of squared relative timing errors. Coefficients are frozen before
scoring either validation group.

| Use | Algorithms | Reason |
|---|---|---|
| Training | integer, REP copy, sort | Sort was studied in the earlier replay and provides independent variation in loads. |
| Historical diagnostics | branch, REP fill | Both algorithms informed earlier model design. |
| Prospective evaluation | lower-bound queries, rolling window sum | Both programs, input scales and independent stdout oracles were fixed before collecting timings. |

Every algorithm runs at scales 1, 2 and 4. The prospective accuracy threshold
requires every one of the 18 metered predictions to fall within ±20% of its
case's native CPU median. Each model is fitted on the same training data and
tested against the same reference times. The original Aonohako split assigned
sort to validation, so these results belong to a new experiment.

For each case and repetition, a native run precedes and follows a pair of
Durante runs. Both use the actual judge client and patched x86-64-v2 runtime
configuration. One run also loads the experimental client, which charges
memory-reading instructions at basic-block entry. Its dynamic instruction
total must equal Durante's in the same process. REP MOVS counts as one load
instruction on a block entry, and its byte demand is a separate production
counter. The experiment accepts only single-threaded, single-image, uninterrupted
runs with identical expected stdout. Native execution uses the host CPU.
Linux wait4 measures direct-child user and system CPU time. The measurements
include launch costs and use a different timing boundary from Aonohako's sandbox.

A fixed seed shuffles the order of cases each round. Each case reverses the
probe order in the next round, with initial order balanced across cases.
The paired CPU times with and without the probe measure the incremental cost
of loading a second client and running its extra basic-block callback.

# Static block-event extension protocol

The selection panel is #114 (three problems, two variants each, deadlines
1.5×, 2× and 3× the same problem's intended solver). The transfer panel is
#116 (six shortest_paths submissions, 65 ms, original reference solver).
Use their registered ELFs, stdin, stdout hashes, two Milan boot archives and
problem-specific ML4 coefficients exactly as recorded. Use local pinned CPU
runs for event counts only. No new native reference times are collected.

Before collecting events, fix four instruction classes using DynamoRIO's
x86 predicates: direct calls, indirect calls, returns and integer division
(DIV or IDIV). Also evaluate the sum of direct and indirect calls. Classify
all original app instructions, including startup and libraries, at block
translation. Every execution charges the static block counts once. No
filter on program, address, symbols, operands, or data-dependent outcomes.
Use two runs in opposite case order per panel, with the registered CPUID
profile. Require a matching output, one uninterrupted image/thread, and
exact instruction-count agreement with the sealed client in each execution.

For each class separately, test frozen ML4 plus a single nonnegative
coefficient k in ns/event. Charge submission and reference identically.
Use independent extrema of the two local event counts and the archived ML4
measurements. Keep each boot's native bounds separate and also replay their
union. Acceptance includes equality. Record intervals of k that get every
native-decisive verdict on all these panels (an indeterminate prediction
does not satisfy a decisive native verdict). Report both the interval
correcting #114's primary 2× set false acceptance alone and its intersection
with every native-decisive #114 constraint. If this latter interval exists,
select its midpoint when bounded on both sides, or twice its positive lower
bound when unbounded above (zero if zero is allowed). Only then apply that k
to #116; report the intersection for all seven holdout roles including the
reference as denominator only. If training is infeasible report the
conflicting constraints and whether any target-correcting k preserves #116.
List contributions and evaluate verdicts of every candidate separately.
Even a passing selection is retrospective and needs new blinded problems.

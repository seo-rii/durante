# Runtime count repeatability

`tests/runtime_repeatability.py` records repeated fresh processes from the
[managed runtime workload suite](RUNTIME_WORKLOADS.md). It asks whether raw work
counts stay within an explicitly selected range for one recorded execution
profile. Successful execution alone does not answer that question.

Java, CPython and Node reuse the existing thread, allocation/GC and child-process
fixtures and exact output/topology checks. Java's process workload includes both
`POSIX_SPAWN` and `FORK`. Each selected workload runs all four existing modes:
native, engine-only, client with REP disabled, and the REP-enabled `durante run`
supervisor. Every repetition starts new processes and includes runtime startup.
There is no discarded warmup, successful-retry replacement or outlier removal.

## Fixed execution profile

The collection records the actual target and wrapper command for every attempt,
the runtime version, source/fixture hashes, selected executable, client, runner,
prepared runtime manifest and core hashes. Java additionally records `javac`,
`libjvm`, `modules`, `jspawnhelper` and the generated class. Before/after input
identity checks must agree. `--runtime-file` adds a known library or module to
this inventory. `--runtime-image sha256:...` records an operator-supplied immutable
image digest; it is deployment metadata, not runtime or host attestation. The
named files are not a complete inventory of every dynamically loaded dependency.

The harness and its children use one allowed logical CPU, defaulting to the
first allowed CPU; select another with `--cpu`. It records affinity, the CPU's
model/microcode/ISA flags and kernel before and after. This fixes CPU placement,
not competing host load, scheduling, ASLR, runtime hash seeds or GC/JIT decisions.
Run campaigns sequentially on an otherwise quiet host when comparing evidence.

The child environment is fixed to `PATH=/bin:/usr/bin` on Linux, `LANG=C`,
`LC_ALL=C`, `TZ=UTC`, and `MALLOC_ARENA_MAX=2`; inherited runtime option variables
are excluded. The selected executable paths are resolved before this change.
Java keeps the workload suite's 64-MiB heap, two active processors, small code and
class reservations and Serial GC. Its tiered JIT is not disabled. Node keeps its
64-MiB old-space limit and explicit GC access, with default JIT behavior. CPython
keeps `-I -S`. These options appear in the profile; a different JIT/GC/ISA profile
requires a different collection. The tool does not use `-Xint` or JIT-off as a
general solution and does not prove that any particular method was compiled.

The [isolate workload evidence](ISOLATED_RUNTIMES.md) uses these same functional
fixtures with a different execution boundary and resource profile. A repeatability
pass here applies only to `run`. It neither imports an isolate pass as a repeated
sample nor qualifies a new isolate profile.

## Collect and evaluate

Use the matching prepared SDK, client and CLI, Linux x86-64, Python 3.11 or newer,
`prlimit`, and a new output directory. The selected runtime is required; a missing
runtime is retained as setup failure with all planned observations unrun.

```sh
python3 tests/runtime_repeatability.py collect \
  --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 --client build/libdurante.so \
  --runner-bin target/release/durante --runtime cpython \
  --workload allocation-gc --repeats 3 --max-spread-ppm 0 \
  --timeout-ms 30000 --campaign-seconds 300 \
  --output-dir .results/repeatability-cpython

python3 tests/runtime_repeatability.py evaluate \
  --input .results/repeatability-cpython/collection.json \
  --output .results/repeatability-cpython/reevaluation.json
```

`--runtime-bin` selects an explicit runtime path. Java also accepts `--javac` and
`--java-major 17|21`. Omit `--workload` for all workloads of the selected runtime,
or repeat it for a subset. Repetitions are bounded to 3–9, the per-invocation
wall limit to 1–180 seconds, and the campaign to 30–1,200 seconds. Defaults are
3 repetitions, a 120-second invocation and a 600-second campaign. The inherited
workload limits include a 2-GiB address space, 90-second CPU limit, 1-MiB output
files and disabled core dumps. Setup probes/compilation have their existing
10/30-second bounds. No observation starts unless its unchanged wall allowance
plus a 15-second collector/cleanup margin fits the remaining campaign budget.
Bounded setup, termination and final provenance checks may finish after the
campaign admission deadline.

The threshold is chosen before collection: `--max-spread-ppm` accepts 0–1,000,000
and defaults to **0**, requiring identical counts. No language-specific default
is supplied. Runtime/GC scheduling can make that strict criterion fail even when
every execution is correct. Re-evaluation uses the recorded threshold and never
changes it. Preserve original collections when choosing a different prospective
profile or threshold; do not relabel the old result as its validation.

`collection.json` is atomically checkpointed after setup and every completed or
failed attempt, preserving the full plan, per-attempt commands, results, errors,
and relative order. Every attempt has its own directory with stdout/stderr and,
when metered, raw records and reports. A failed program remains in the plan and
later attempts still run. An incomplete cleanup or harness exception blocks the
remaining attempts and records their unrun reasons. An interrupted campaign's
last checkpoint remains unqualified; it can be evaluated without re-executing.

`evaluation.json` distinguishes:

| Field | Meaning |
| --- | --- |
| `collection_complete` | Every planned attempt returned a result, including failed program results. |
| `collection_passed` | Setup, profile/provenance, every control, output/topology oracle, measurement and cleanup passed. |
| `qualification_passed` | Collection passed and every required raw counter spread met the recorded threshold. |

The command exits 0 only for `qualification_passed: true`, otherwise 1 when a
collection/evaluation result was produced; invalid CLI/file input exits 2.
`evaluate` checks retained result structure, commands, profile identity and
integer partitions. It does not rerun the runtime or independently attest saved
JSON. Raw Rust/Python collector agreement is checked during collection by the
existing workload harness. Keep the complete output directory as evidence.

## Exact statistics and coverage

Each workload and metered mode is evaluated separately. The metrics are
instructions, combined REP bytes/entries, unsupported REP entries, signals
observed, and MOVS/STOS bytes/entries. All tree, process and image partitions must
be complete, consistent uint64 values. Missing legacy operation detail is
unavailable, not measured zero; it cannot qualify operation-level repeatability.
REP-off observations must explicitly contain zero REP demand.

For each metric, let `R = maximum - minimum` and `M` be the exact median. For an
even number of observations the median may have denominator 2. The gate is:

```text
R * M.denominator * 1,000,000 <= max_spread_ppm * M.numerator
```

Python integers and rational numbers keep values above 2^53 and uint64 boundary
values exact. The report stores median and spread as numerator/denominator pairs;
no floating-point rounding participates in a decision. All-zero samples have
zero spread. A zero median with a nonzero range has undefined relative spread
and fails. A missing or invalid member suppresses that whole group's statistics;
the evaluator never reports a passing subset as a complete repeated sample.

A repeatability pass does not widen signal or REP coverage eligibility. Unsupported
REP work stays unsupported, handled signals remain visible, and counts remain
application-mutable diagnostics. There is no conversion to CPU nanoseconds,
language coefficient, production time limit, or guarantee for other workloads.

## Bounded local observation

On 2026-11-15, the unchanged implementation at `2be6c66` collected CPython 3.11.2
`allocation-gc` on one allowed logical CPU, using three repetitions of each mode,
a 30-second invocation limit, a 300-second campaign and the preselected **0 ppm**
threshold. All 12 executions passed their output, accounting and cleanup checks;
the recorded inputs and CPU profile were unchanged. Collection passed, but
repeatability qualification failed because instruction totals varied:

| Metered mode | Minimum instructions | Exact median | Maximum instructions |
| --- | ---: | ---: | ---: |
| Client, REP disabled | 92,826,902 | 92,848,532 | 92,938,573 |
| Supervisor, REP enabled | 92,882,628 | 92,931,323 | 93,090,569 |

Other recorded metrics, including combined and split REP work, were identical
within each three-observation group. Offline re-evaluation reproduced the exact
evaluation and its exit status 1. No failed member was removed and no threshold
was changed. This small observation demonstrates the distinction between
successful collection and repeatability; it neither identifies the source of
instruction variability nor establishes a language-wide variability bound.

The synthetic regression suite needs no runtime build:

```sh
python3 -m unittest discover -s tests -p 'test_runtime_repeatability.py' -v
```

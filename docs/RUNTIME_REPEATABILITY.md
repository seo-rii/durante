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

New CPython collections also run the workload harness's native implementation
probe before any counted workload: the selected executable must report
`sys.implementation.name == "cpython"`. A PyPy executable cannot be admitted as
a CPython profile, including the default isolated hash profile. The command and
result are retained in `implementation-cpython/probe.json` and
`profile.versions.cpython.implementation_probe`. This is separate from the
hash-control probe below and is not a measured repetition. Existing collections
are unchanged; missing historical implementation evidence is not added retroactively.

The harness and its children use one allowed logical CPU, defaulting to the
first allowed CPU; select another with `--cpu`. It records affinity, the CPU's
model/microcode/ISA flags and kernel before and after. This fixes CPU placement,
not competing host load, scheduling, ASLR, runtime hash seeds or GC/JIT decisions.
Run campaigns sequentially on an otherwise quiet host when comparing evidence.
New collections also record the harness's Linux process-personality value and
its `ADDR_NO_RANDOMIZE` bit. Controlled CPython startup probes record that same
state after native exec and must agree with the harness. The probe has an
explicit version; old collections retain their original probe and unknown
personality state rather than being labeled ASLR enabled or disabled retroactively.

The child environment is fixed to `PATH=/bin:/usr/bin` on Linux, `LANG=C`,
`LC_ALL=C`, `TZ=UTC`, and `MALLOC_ARENA_MAX=2`; inherited runtime option variables
are excluded. The selected executable paths are resolved before this change.
Java keeps the workload suite's 64-MiB heap, two active processors, small code and
class reservations and Serial GC. Its tiered JIT is not disabled. Node keeps its
64-MiB old-space limit and explicit GC access, with default JIT behavior. CPython
keeps `-I -S`. These options appear in the profile; a different JIT/GC/ISA profile
requires a different collection. The tool does not use `-Xint` or JIT-off as a
general solution and does not prove that any particular method was compiled.

## Explicit CPython hash controls

The default CPython command remains `-I -S`. Isolated mode implies ignoring
`PYTHON*` startup variables, so adding `PYTHONHASHSEED` alone cannot control that
profile. The workload also imports its standard-library dependencies before
`gc.disable()`; counts include interpreter startup, imports and shutdown.
Equal output and a fixed CPU do not establish identical interpreter work.

For a bounded diagnostic comparison, `--cpython-hash-profile random|seed-1`
explicitly selects `-P -s -S` and respectively `PYTHONHASHSEED=random` or `1`.
Both controls use the same flags, fixture, resources and otherwise identical
fixed environment. All inherited environment values are removed; the process
launcher reintroduces only the selected hash variable after its usual removal
of `PYTHON*` values. This option requires `--runtime cpython` and explicit
selection of `allocation-gc`, `startup` or `imports`. Child-process fixtures
keep their own isolated startup flags and are outside this control. Omission
or `isolated` preserves the original command and environment.

A separate native startup probe retains the effective `sys.flags`, hash
algorithm and sentinel, selected executable, forwarded Python environment, and
reported module source/cache paths. Those named module files join before/after
provenance. The probe output, execution result and command remain in
`collection.json` and `cpython-startup-probe/`; missing or inconsistent control
input fails evaluation. This is a probe of the same startup inputs, not a capture
of every measured process's random seed or every loaded library. Named file
hashes remain reproducibility evidence rather than full runtime attestation.

Predeclare two fresh collections with the same three repeats, four modes and
**0 ppm** threshold, changing only the hash control (`random` then `seed-1`).
Run both plans once, retain all 24 workload observations and stop regardless of
the result. Each collection also has its normal version probe and one startup
probe. A reduction in spread would support hash randomization as a contributing
input; residual spread alone would not identify a counter error. These explicit
profiles differ from the default isolated profile and cannot approve that
profile or arbitrary CPython programs. ASLR, address-dependent behavior, GC and
other uncontrolled runtime decisions may still matter.

## CPython startup and import controls

The repeatability tool additionally accepts explicit `--workload startup` and
`--workload imports` selections for CPython only. These controls are absent from
the default plan and from the ordinary language compatibility matrix. `startup`
executes an inline program that prints one fixed line; `imports` imports the same
`gc`, `pathlib`, `subprocess`, `sys` and `threading` modules as the allocation
fixture, then prints a different fixed line. Neither runs the allocation loop
or its explicit collections. The original fixture is unchanged.

Each complete inline program, exact output and single-process/image oracle is
recorded in the profile. The evaluator reconstructs those fixed commands and
keeps statistics separate by workload and execution mode. Runtime, harness and
named module files retain before/after hashes. The native startup probe remains
separate from the measured processes; it does not prove their GC or allocator
states. Counts include the loader, interpreter initialization, printing and
shutdown. These are complete-process controls, not measurements of separate
regions, and subtracting their independent totals would not produce an exact
allocation-loop count.

A bounded follow-up to the hash comparison selects both controls with `seed-1`,
three repetitions, all four modes and the same **0 ppm** gate. This declares
24 observations before execution. Keep all results and stop after that plan;
do not add repetitions based on a promising range. The earlier allocation
collection is historical context, not a simultaneous paired sample. Variation
in these smaller controls would show that the allocation loop is unnecessary
for observing spread, but would not by itself distinguish actual startup work
from a counter error. A fixed three-sample result would not qualify the original
allocation fixture or other programs.

```sh
python3 tests/runtime_repeatability.py collect \
  --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 --client build/libdurante.so \
  --runner-bin target/release/durante --runtime cpython \
  --workload startup --workload imports --cpython-hash-profile seed-1 \
  --repeats 3 --max-spread-ppm 0 --timeout-ms 30000 --campaign-seconds 300 \
  --output-dir .results/repeatability-cpython-startup
```

The [isolate workload evidence](ISOLATED_RUNTIMES.md) uses the ordinary functional
workload fixtures with a different execution boundary and resource profile.
It does not cover the opt-in startup/import controls. A repeatability pass here
applies only to `run`. It neither imports an isolate pass as a repeated sample
nor qualifies a new isolate profile.

## Per-process address-layout comparison

On a host that permits the existing `setarch x86_64 -R` command, an operator can
start the entire collection under that wrapper. This sets the inherited
`ADDR_NO_RANDOMIZE` process flag; it does not change the host's ASLR configuration.
Record the full wrapper command and hash the wrapper binary as a named input:

```sh
setarch x86_64 -R python3 tests/runtime_repeatability.py collect \
  --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 --client build/libdurante.so \
  --runner-bin target/release/durante --runtime cpython \
  --workload startup --workload imports --cpython-hash-profile seed-1 \
  --runtime-file /usr/bin/setarch \
  --repeats 3 --max-spread-ppm 0 --timeout-ms 30000 --campaign-seconds 300 \
  --output-dir .results/repeatability-cpython-address-control
```

The harness and separate native CPython preflight must report the same numeric
personality and flag. These observations describe inherited process state, not
the addresses of every instrumented process or deterministic allocator behavior.
No global setting or permanent runtime option is changed. If the host denies
the wrapper, retain that failure; do not silently run with ordinary layout.
Predeclare a fresh bounded comparison and keep the original observations. A
reduction in spread would support layout as a contributing input; neither a
reduction nor an exact finite sample establishes language-wide repeatability or
rules out counter defects. Earlier observations that did not record personality
remain unknown, even when a later control explicitly records this flag.

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

Offline evaluation reconstructs CPython and Node fixture commands using their
recorded source-provenance paths, so moving the evaluator to another checkout
does not change the recorded result. It does not open or execute those fixture
paths. Missing or inconsistent fixture provenance, changed commands and changed
input identities still fail; the recorded profile and threshold remain unchanged.

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

## Bounded CPython hash-control comparison

The 2026-11-15 follow-up ran the predeclared `random` then `seed-1` profiles once
each: three repetitions of all four modes, CPU 0, 30-second invocation limit,
300-second campaign, and **0 ppm** throughout. Both collections completed and
passed all 12 functional/accounting/cleanup observations. The effective probes
reported `isolated=0`, `ignore_environment=0`, `safe_path=true`, and exactly the
selected `PYTHONHASHSEED`. Commands, other environment values, CPU profile and
named input hashes were equal between collections. Both retained the same 80
module source/cache paths plus five explicitly named shared-library/loader files;
all captured inputs and the CPU profile were unchanged before/after execution.

| Hash control | Metered mode | Minimum instructions | Exact median | Maximum instructions | Range |
| --- | --- | ---: | ---: | ---: | ---: |
| `random` | Client, REP disabled | 92,788,997 | 92,817,489 | 93,168,221 | 379,224 |
| `random` | Supervisor, REP enabled | 92,968,616 | 92,976,900 | 93,048,953 | 80,337 |
| `seed-1` | Client, REP disabled | 92,823,785 | 92,852,062 | 92,909,468 | 85,683 |
| `seed-1` | Supervisor, REP enabled | 92,823,722 | 92,823,746 | 92,867,165 | 43,443 |

Other recorded counters had zero range within every group. Both profiles failed
exact repeatability and exited 1; offline evaluation reproduced both results
exactly. The original isolated collection also retained its previous evaluation
semantics. All 24 workload observations were kept; neither profile was retried
and no threshold was changed.

Instruction spread decreased in this small fixed-seed comparison, but remained
nonzero. A fixed string-hash seed therefore did not establish exact repeatability.
The experiment does not identify the residual source or establish a counter
error, and it does not qualify the default isolated profile. The startup probe
is separate from the measured workload; its hash sentinel is not an observation
of every measured process's internal state.

## Bounded startup/import observation

The subsequent 2026-11-15 plan ran `startup` and `imports` once as one declared
24-observation collection: `seed-1`, CPU 0, three repetitions of four modes,
30-second invocation limit, 300-second campaign and **0 ppm**. It reused the
previous comparison's CPython 3.11.2 installation, prepared runtime, client,
CLI and named libraries. These binaries predate the CPU-policy client change
in main; this is continued diagnosis with fixed inputs, not qualification of
that newer client. All 24 executions passed their exact output, accounting and
cleanup checks, with unchanged input hashes and CPU profile. The collection
took 23.635 seconds and returned 1 because all four metered groups retained
instruction spread:

| Control | Metered mode | Minimum instructions | Exact median | Maximum instructions | Range |
| --- | --- | ---: | ---: | ---: | ---: |
| `startup` | Client, REP disabled | 19,657,279 | 19,693,463 | 19,706,543 | 49,264 |
| `startup` | Supervisor, REP enabled | 19,657,353 | 19,693,457 | 19,693,583 | 36,230 |
| `imports` | Client, REP disabled | 75,434,391 | 75,449,165 | 75,487,341 | 52,950 |
| `imports` | Supervisor, REP enabled | 75,487,102 | 75,487,270 | 75,487,412 | 310 |

Every other recorded counter had zero range in each group. The planned runs
were retained without retry or threshold change. The allocation loop and its
explicit collections are therefore unnecessary to observe spread in this
installation. Actual interpreter/loader/shutdown work and counter behavior
remain possible contributors; this experiment does not separate them.

A separate six-execution native probe observed identical automatic-GC collection
counts across three fresh processes during the fixture's imports; disabling GC
before those imports suppressed their additional collections. Allocated-block
counts and object addresses still differed. Those probes used extra observation
code and were not the measured workload, so they neither establish GC as the
cause nor rule out address-dependent runtime work. The original isolated and
both hash-control collections retain the same evaluation decisions and numeric
results, with only the additional explanatory limitation for these new controls.

## Bounded address-layout observation

The next declared plan started the same 24 startup/import observations under
`/usr/bin/setarch x86_64 -R`, keeping the selected CPU, hash seed, modes, measured
programs, binaries, repeat count and **0 ppm** threshold. The full wrapper command
and its binary identity were retained, with the binary also included in the
collection's before/after inputs. The new versioned native probe and harness
both reported personality `262144` with `ADDR_NO_RANDOMIZE=true`; that control
was explicitly checked before interpreting the result. No host setting changed.

All 24 observations again passed output, accounting and cleanup. Source, named
inputs, wrapper and CPU profile remained unchanged. Collection took 23.737
seconds. Instruction ranges were smaller in this finite comparison:

| Control | Metered mode | Minimum instructions | Exact median | Maximum instructions | Range |
| --- | --- | ---: | ---: | ---: | ---: |
| `startup` | Client, REP disabled | 19,657,227 | 19,657,227 | 19,657,251 | 24 |
| `startup` | Supervisor, REP enabled | 19,657,227 | 19,657,227 | 19,657,227 | 0 |
| `imports` | Client, REP disabled | 75,434,010 | 75,434,010 | 75,434,094 | 84 |
| `imports` | Supervisor, REP enabled | 75,434,010 | 75,434,028 | 75,434,041 | 31 |

Every other counter had zero range per group. The overall repeatability result
still failed and returned 1 because three instruction groups exceeded 0 ppm;
the one exact group does not qualify the whole plan. All observations remain
present without retry or threshold change, and offline evaluation reproduces
the result exactly.

The earlier startup/import collection did not record personality and retains
that unknown state; it was not relabeled or rerun. The new metadata/probe differs
from that historical collection, and these small sequential samples do not
isolate every changing runtime input. The observed reduction supports further
layout-controlled investigation, but neither establishes a causal share for
ASLR nor resolves the remaining variation, proves counter correctness or
qualifies the original allocation workload. Matching harness/native flags also
does not prove fixed addresses inside every instrumented execution.

## Bounded effective-profile observation

A separate diagnostic on 2026-11-15 checked the controls inside instrumented
CPython processes. The collection tool's existing startup probe runs once
natively; that probe alone cannot establish the effective state of the later
engine, client and supervisor targets. The additional diagnostic used the
unchanged repository code at `18dbb3f` and the same CPython 3.11.2 installation,
prepared SDK, client, CLI, named libraries, CPU 0, `-P -s -S` flags,
`PYTHONHASHSEED=1` and `setarch x86_64 -R` wrapper. The older fixed client remains
separate from the later CPU-policy client on main.

The plan declared three rounds, each in native, engine-only, REP-off client and
REP-enabled supervisor order: twelve fresh processes in total, with no retries.
It kept the 30-second target wall limit, five-second supervisor publication
allowance, 90-second CPU limit, 2-GiB address space, 1-MiB files, disabled core
dumps and 300-second campaign. A row required its full wall allowance plus
23 seconds reserved for collection, publication, termination and owner cleanup.
A failed native control or incomplete cleanup would leave later rows unrun and
visible in the result.

The fixed diagnostic program imported the startup probe's modules and retained
seven objects: `None`, the `sys` and `builtins` modules, `sys.modules`, a new
`object()`, a `bytearray(b"durante-effective-profile-v1")`, and a list holding the
last two objects. It recorded their addresses and only the mappings containing
those addresses. Reading `/proc/self/maps` had a 256-KiB bound; at most seven
selected mappings could enter the output. One JSON line, limited to 16 KiB,
also recorded the declared startup flags, named hash input, hash sentinel and
algorithm, process personality, module paths and a GC/allocated-block snapshot
after imports and sentinel creation but before reading maps. The program did
not dump the process environment.

All twelve rows completed and passed in 26.26 seconds. Every mode reported the
declared seed `1`, hash sentinel `8559364366264786793`, `siphash13`, startup flags
and personality `262144` with `ADDR_NO_RANDOMIZE=true`. The seven selected object
addresses and their containing mappings were identical across all three
observations within each mode. Native addresses differed from engine/client
addresses, which the diagnostic allowed. GC generation counts/statistics and
allocated-block counts were also identical within each mode at the sampled
point: 29,014 blocks natively and 29,031 in each instrumented mode. Each stdout
was 6,118 bytes and its bytes were identical within its mode.

The metered rows passed the existing independent raw-record, one-process,
one-image and counter-partition checks. Named runtime, standard-library,
probe/driver, SDK, client, CLI and wrapper identities were unchanged before and
after execution. Raw stdout and reports were retained; cleanup, CPU-profile
checks and affinity restoration completed. This diagnostic's exit 0 means its
declared control and accounting observations succeeded. It does not change the
repeatability evaluator's qualification result: both earlier 24-observation
startup/import collections still fail their original **0 ppm** gate and retain
their unchanged records and exit status 1.

The extra imports, allocations, mapping reads and output make this a different
program from the startup/import controls. It parses the complete bounded maps
view before selecting the containing mappings, so stable selected output does
not establish that every mapping or runtime input was fixed. A snapshot of
seven objects and GC state also does not cover every allocation or the full
execution history. These observations confirm the selected controls inside
these instrumented processes; they neither identify the original instruction
spread's cause nor establish counter correctness or language-wide repeatability.
Those observations did not locate the residual variation. The following
location diagnostic distinguishes actual clock paths in a new complete sample.

## Instruction-location follow-up

The 2026-11-16 follow-up retained the exact startup/import Python programs,
CPython 3.11.2, `-P -s -S`, seed `1`, CPU 0 and the `setarch x86_64 -R` control.
A test client based on main `0318100` additionally recorded each observed BB
entry by PC and instruction length, resolving its loaded ELF GNU build-id and
module-relative offset at exit.
Its weighted histogram had to equal the complete one-process, one-image,
one-thread counter report, with zero replay rollbacks and independent collector
agreement. A static assembly oracle independently required 26 instructions;
its loaded build-id also matched `readelf`'s inspection of the file.

The first declared six-row diagnostic completed in 20.48 seconds. All startup
and import executions passed output and accounting checks, but the three import
histograms reached the 32,768-entry observation cap. They remain diagnostic
failures, and the whole attempt retains exit 1. The three complete startup
histograms each contained 32,450 entries and 19,657,227 instructions. These
finite startup observations do not qualify the original repeatability plan.

A separate version increased only the histogram storage capacity to 65,536
entries. It retained the 1-MiB file bound, 30-second row wall limit, 90-second
CPU limit and 300-second campaign admission budget. Three newly declared import
observations completed in 8.14 seconds; every histogram was complete, contained
41,550–41,552 entries and occupied at most 645,694 bytes. All named inputs,
CPU-profile checks, collector comparisons, cleanup and affinity restoration
passed. No earlier failed row was replaced or reclassified.

Every changing BB in this complete sample belonged to `linux-vdso.so.1`, GNU
build-id `ffe081a9105536e4e18729470ad623c16ac6de8f`, on kernel
`6.1.0-54-cloud-amd64`. All other module/offset/length entry counts were identical.
Disassembly of the matching vDSO identified `__vdso_clock_gettime`'s sequence
recheck and nanosecond-normalization paths. All three rows entered and returned
from that function 566 times. A sequence change repeated a 31-instruction path;
one normalization pass executed six additional instructions relative to its
direct-return path.

| Import observation | Instructions | Extra sequence passes | Normalization passes |
| --- | ---: | ---: | ---: |
| 1 | 75,434,041 | 1 | 0 |
| 2 | 75,434,070 | 0 | 10 |
| 3 | 75,434,082 | 0 | 12 |

These are direct observations of changing locations, rather than subtraction of
separate whole-program experiments. Summing the recorded vDSO BB charges gives
32,867, 32,896 and 32,908 instructions; every other recorded module contributes
the same 75,401,174 instructions. The changing clock paths account for all
observed spread in these three rows. They are actual time-dependent user-space
work, not evidence of a counter arithmetic defect. Their clock BBs remain part
of the metric: neither the clock nor its count is suppressed or frozen.

Those initial diagnostics did not observe module unloads, so exit-time identity
could not exclude an intervening unload and address reuse. The final test client
adds an unload callback and rejects such a histogram explicitly. A separately
declared three-row import validation then completed in 8.49 seconds with zero
unloads, 41,550 entries and 645,670 bytes per histogram. Counts were 75,434,010,
75,434,041 and 75,434,010. Only the seven BBs in the same sequence-retry path
changed: their extra 2 + 5 + 3 + 2 + 3 + 8 + 8 instructions account for the whole
31-instruction difference. Every other BB count was identical, and direct
non-vDSO charges remained 75,401,174 in each row. The independent 26-instruction
oracle passed, and a real `dlopen`/call/`dlclose` fixture was rejected specifically
for its one module unload despite valid output and lifecycle accounting. Named
inputs, cleanup, CPU-profile checks and affinity restoration all passed. The
earlier samples remain separate; the new guard does not attest their lifetimes
retroactively.

The histogram observes the counter's existing insertion calls, so sum agreement
is a consistency check, not an independent proof of every runtime count. The
assembly oracle and matching disassembly supply separate checks. Instrumentation
also changes execution timing and can change which clock paths occur. Earlier
whole-process ranges such as 24, 84 and 31 are consistent with these path costs,
but those older rows have no location histograms and their causes are not
retroactively assigned. The original isolated/allocation/hash-control results
and their **0 ppm** failures remain unchanged. This identifies a residual
mechanism in the controlled sample; it does not establish exact repeatability
for every CPython program or explain all earlier address-dependent spread.

## Reproducing location diagnostics

Build the separate test client and its independent oracle against the prepared
SDK. The normal client does not compile the histogram code or expose a profiling
option:

```sh
cmake -S tests/count_locations -B build-count-locations \
  -DCMAKE_BUILD_TYPE=Release \
  -DDynamoRIO_DIR="$PWD/toolkit/sdk/DynamoRIO-Linux-11.3.0-1/cmake"
cmake --build build-count-locations -j2
setarch x86_64 -R python3 tests/count_locations.py \
  --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 \
  --client build-count-locations/libcount_locations.so \
  --runner-bin target/release/durante \
  --oracle build-count-locations/count_locations_oracle \
  --unload-oracle build-count-locations/count_locations_unload_oracle \
  --unload-module build-count-locations/libcount_locations_unload_module.so \
  --workload imports --cpu 0 --output-dir .results/count-locations
```

Choose an allowed CPU and a fresh output directory. The tool first checks the
native/instrumented assembly oracle, an expected-rejection `dlclose` oracle and
a separate native startup probe, then
runs exactly three observations of each selected startup/import control. Use
repeatable `--runtime-file PATH` arguments for additional named loader/library
inputs. The probe inventories its standard-library files; it does not attest
every later process state or loaded dependency. Inherited `PYTHON*` variables
remain removed except the explicit seed-1 control.

`count-locations.tsv` is retained beside each row's raw lifecycle records.
Capture is bounded by 65,536 distinct BB/length entries, 128 modules and 1 MiB;
unknown module identity, any observed module unload, overflow, replay, extra
threads or incomplete capture
prevent an accepted comparison. The tool preserves failed and unrun rows and
does not retry. Its `passed` means complete diagnostic capture and accounting,
with `qualification_claim: false`; it never changes the repeatability gate or
requires time-dependent variation to appear in every finite sample.

Module identities are resolved at exit. The test client therefore observes
module unloads from initialization and rejects the histogram if any occur;
reloading another image at the same address cannot silently inherit earlier
BB charges. This diagnostic covers loaded ELF modules, not arbitrary generated
or self-modifying code.

CI uses `--oracle-only` to exercise the actual diagnostic client, ELF identity
reader, independently known 26-instruction count and rejection of a real
`dlopen`/call/`dlclose` fixture. It does not depend on a particular clock phase
or permit a count-spread exception.

## Tests

The regression suite uses small native CPython probes but needs no runtime build:

```sh
python3 -m unittest discover -s tests -p 'test_runtime_repeatability.py' -v
python3 -m unittest discover -s tests -p 'test_count_locations.py' -v
```

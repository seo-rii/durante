# SIMD and runtime qualification

Durante measures application machine instructions at basic-block entry. Runtime
qualification checks that selected programs retain their native outputs and exit
behavior, and that their process/image records are complete. It does not certify
a language, an ISA extension, or all programs using either.

## Measurement semantics

An interpreter's dispatch loop, allocator, garbage collector and native library
code all contribute machine instructions. Counts are not Python bytecodes,
Java bytecodes, source statements or abstract operations. A JIT can reduce counts
by compiling the same computation into fewer machine instructions. Warmup and
runtime version therefore affect the result.

A scalar instruction and a SIMD instruction each contribute one instruction.
Vector width, active mask lanes and the number of arithmetic operations are not
weights. A wider instruction may process more elements at the same count; its
latency and throughput also depend on the CPU. Cache misses, memory stalls and
kernel work do not acquire a proportional charge. REP MOVS/STOS requested bytes
are a separate metric, not an implicit SIMD or timing weight.

The tests below check counting and preservation within these semantics. They do
not define a CPU-time conversion, cross-language normalization or trusted TLE
policy. Counters remain application-mutable; see [SECURITY.md](../SECURITY.md).

## SIMD checks

The native fixture uses fixed assembly and a closed-form instruction-count
oracle. Each supported case runs natively and under the pinned runtime/client,
with REP telemetry both disabled and enabled. The harness compares output and
exit status as well as the aggregate count. Scalar/SSE2/AVX2/AVX-512 coverage is
reported per case rather than inferred from successful ordinary C execution.

Feature selection checks both CPU instruction support and OS-enabled extended
register state before executing vector code. Unsupported features are explicit
skips, never successful coverage. AVX-512 cannot be qualified on an AVX2-only
machine. The per-run report is the authority for which fixtures actually ran.
An OS or CPU upgrade can change that matrix. All four encodings are assembled
and their static instruction counts checked even when execution is unavailable;
that build check does not qualify execution on the missing ISA.

The fixtures cover integer and finite double-precision addition, vector/flag
state across basic-block boundaries, and AVX-512F mask merge/zero behavior. They
do not claim all instructions in SSE/AVX extensions are qualified. The static
whole-program oracles are scalar 87, SSE2 39, AVX2 28 and AVX-512F 37 instructions;
the latter performs additional mask checks. Exact output bytes and normal exit
codes 0 and 7 must survive instrumentation. To exercise REP-off through the
ordinary supervisor, a test-only launcher adapter removes its `-rep_bytes` flag
before the target delimiter. The production supervisor still enables REP as
before.

## Interpreter and JIT checks

The runtime suite compares deterministic fixture outputs with ordinary execution
and engine-only controls before accepting instrumented results. It exercises
selected CPython/PyPy arithmetic, hashlib/zlib and subprocess paths, Node default
and `--jitless` modes, and Java tiered and `-Xint` modes. Standard-library native
extension coverage does not qualify NumPy, BLAS or every native extension. Presence requirements in CI prevent missing runtimes from being
silently treated as coverage. Functional outputs and process-tree completeness
are checked; instruction totals between different execution modes need not agree.

Instruction accounting and REP-byte telemetry have separate qualification
states. A valid complete instruction report may observe REP forms outside the
MOVS/STOS byte extension. Such a run retains its exact
`unsupported_rep_entries` and an explicit partial-telemetry label; it does not
claim complete REP-byte coverage. Node 22 on the CI host exercised this case in
both default and `--jitless` modes. REP-off must still expose no REP telemetry,
and malformed, inconsistent or incomplete accounting remains a failure.

A failed instrumentation case is not a successful runtime qualification merely
because its counter is invalid. Known unsupported cases are reported separately
from qualified passes and skipped runtimes. In particular, Java startup previously
crashed with this client while an engine-only control succeeded. Using a DynamoRIO
clean call for the entry counter preserves application state and fixes the
reproduced OpenJDK 17.0.21 startup crash. The suite includes
a `java -version` startup fixture plus tiered and `-Xint` arithmetic fixtures,
each with native/engine-only controls and REP off/on. CI now requires all Java
fixtures to pass via `--require-qualified-runtime java`; the historical
known-unqualified classification cannot satisfy that requirement. Java probes
remain diagnostic even after their particular paths pass.
A newly passing fixture establishes only that fixture on its reported runtime.

HotSpot deliberately handles startup faults while probing CPU/OS state (see its
[CPU probe](https://github.com/openjdk/jdk17u/blob/master/src/hotspot/cpu/x86/vm_version_x86.cpp)
and [Linux signal handler](https://github.com/openjdk/jdk17u/blob/master/src/hotspot/os_cpu/linux_x86/os_linux_x86.cpp)).
Successful Java fixtures therefore retain `signals_observed` quality instead of
requiring zero signals. Output, normal exit, cleanup and complete component sums
remain mandatory. This does not turn interrupted BB-entry charging into exact
retired-instruction accounting. Each runtime invocation is still bounded; CI
allows 40 seconds because interpreted Java runs substantially more instrumented
work than its native control.

The independent native `signal_context.c` regression also checks registers and
flags that linear analysis would consider dead: a SIGSEGV handler inspects the
saved state and resumes past a future clobber. The old client changes that state.
The counter therefore uses clean-call state preservation for every block.
A restoration barrier alone fixes Java startup but does not preserve dead flags
in this native regression; the pinned drreg implementation elides their save. Both direct REP modes and the supervisor must preserve exact
fixture output and record both handled signals. These tests do not establish all
possible signal-context transitions as supported.

[Signal vector and floating-point state qualification](SIGNAL_STATE.md) adds
nested alternate-stack and bounded asynchronous delivery checks, including
vector register contexts, x87 and MXCSR state, with native/engine/client controls
for both release and debug runtimes. ISA availability and skips remain explicit.

Optional `durante isolate` execution has additional syscall, filesystem and
resource restrictions. Passing these ordinary execution tests does not establish
compatibility with that separate profile.

## Reading results

Retain the JSON reports alongside the tested source revision and runtime versions.
Check failed cases, unsupported modes, skipped features and cleanup status before
using the pass count. A zero harness exit means its stated qualification policy
was satisfied; it does not turn diagnostic or unsupported rows into support.
CI retains `.results/` artifacts, including baseline and instrumented diagnostics.

Runtime qualification records `input_provenance` before version probes,
compilation or fixture execution, then checks the same paths after cleanup.
It includes the harness/reference/fixture sources, release DynamoRIO core,
Durante client and collector CLI, `drrun`, the selected runtime executables,
`javac` when Java is used, and the `prlimit` wrapper. Executable lookup happens
once: the recorded absolute paths are also used for subprocess invocation.
Each file has before/after SHA-256, resolved path, inode and metadata identities;
reads reject nonregular inputs and are capped at 256 MiB per file.

`inputs_unchanged` must be true for successful qualification. Detected replacement,
content or metadata changes, deletion, and inability to inspect an input produce
exit 1 and a named entry in `input_provenance.errors`, even if all fixtures passed.
Missing required artifacts fail before execution and still produce `results.json`
when a fresh writable output directory is available. Unavailable optional
runtimes remain explicit skips, subject to the requested presence/qualification
requirements. The existing `inputs` and `binary_sha256` fields retain initial
hashes; `sources_unchanged` covers only the source subset.

These checks are reproducibility evidence, not attestation against a hostile
host. Keep installations immutable for the entire run. Changes restored between
observations can evade detection, and shared libraries, whole runtime trees and
generated bytecode are not exhaustively fingerprinted. A matching before/after
snapshot does not make the instruction counters trusted.


## Runtime execution profile

The interpreter/JIT compatibility harness and managed workload suite use the
canonical Durante profile: `-disable_traces -vm_size 32M -vmheap_size 32M`.
The engine-only control uses the same options as the counter modes. The CLI also
disables DynamoRIO's trace-building optimization. Basic blocks are still charged
at entry; this setting does not turn the metric into retired instructions.

Trace-enabled execution is outside the managed-runtime qualification. The added
Java threading/GC checks exposed late thread-state errors and a runtime crash in
that configuration. A passing basic-block profile does not qualify the trace
optimizer, arbitrary GC/JIT configurations, or all language libraries. See
[managed runtime workloads](RUNTIME_WORKLOADS.md) for the strict workload controls,
process topology checks and required Java 17/21 CI jobs.

## Reproduce the checks

After building the pinned SDK, client and release CLI as described in the README,
install the required runtimes and set `JAVA_HOME_17_X64` to the trusted JDK 17
installation root. GitHub's qualification runner provides that variable; local
shells must set it explicitly. The full matrix uses the same Java gate and
40-second per-invocation deadline as CI:

```sh
: "${JAVA_HOME_17_X64:?Set this to a JDK 17 installation}"
python3 tests/simd.py --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 \
  --client build/libdurante.so --runner-bin target/release/durante \
  --output-dir .results/simd --require-isa sse2 --repeats 2
python3 tests/runtime_compat.py --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 \
  --client build/libdurante.so --runner-bin target/release/durante \
  --output-dir .results/runtime-compat --require-runtime cpython \
  --require-runtime pypy --require-runtime node --require-qualified-runtime java \
  --java "$JAVA_HOME_17_X64/bin/java" --javac "$JAVA_HOME_17_X64/bin/javac" \
  --timeout-ms 40000
```

Use fresh output directories. For a local partial run, omit requirements for
unavailable runtimes and, if Java is omitted, the JDK variable check and explicit
Java paths. Missing runtimes remain visible as skips. `--require-runtime java`
checks presence but can accept a historical `known-unqualified` result;
`--require-qualified-runtime java` requires every Java fixture to pass. CI pins
its qualification runner to Ubuntu 24.04 and requires all four runtimes.
Pinned setup actions select cached PyPy 7.3.19/Python 3.10 and CPython 3.12; the
harness runs under CPython. Java uses the runner's explicit `JAVA_HOME_17_X64`
installation. The job verifies the preinstalled compiler, CMake, patch and Node
instead of refreshing OS packages. Missing required tools fail immediately;
setup steps have deadlines. Actual runtime versions remain in each report. SIMD baseline coverage is required;
AVX2 and AVX-512 execution depends on that runner's CPU and OS feature mask.
Running a runtime with JIT enabled does not prove a particular hot function was
compiled. The separately asserted generated-code fixture in `tests/smoke.py`
provides a smaller, deterministic check that executed JIT bytes are counted.

[Generated-code lifecycle qualification](JIT_LIFECYCLE.md) adds permission
transitions, RWX replacement and same-address remapping. Separate strict replay
fixtures cover scalar writes, modification inside the current block, partial
forward/backward MOVSB and STOSB, and repeated rewrites across the runtime's
adaptive threshold. Native/release/debug controls check application behavior;
every counter result must match independent instruction and REP oracles. There
is no known-overcount exception. Unknown or ambiguous recovery fails closed.

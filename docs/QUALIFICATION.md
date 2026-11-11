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
crashed with this client while an engine-only control succeeded; Java probes must
remain diagnostic until their particular native/control/instrumented paths pass.
A newly passing fixture establishes only that fixture on its reported runtime.

Optional `durante isolate` execution has additional syscall, filesystem and
resource restrictions. Passing these ordinary execution tests does not establish
compatibility with that separate profile.

## Reading results

Retain the JSON reports alongside the tested source revision and runtime versions.
Check failed cases, unsupported modes, skipped features and cleanup status before
using the pass count. A zero harness exit means its stated qualification policy
was satisfied; it does not turn diagnostic or unsupported rows into support.
CI retains `.results/` artifacts, including baseline and instrumented diagnostics.


## Reproduce the checks

After building the pinned SDK, client and release CLI as described in the README:

```sh
python3 tests/simd.py --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 \
  --client build/libdurante.so --runner-bin target/release/durante \
  --output-dir .results/simd --require-isa sse2 --repeats 2
python3 tests/runtime_compat.py --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 \
  --client build/libdurante.so --runner-bin target/release/durante \
  --output-dir .results/runtime-compat --require-runtime cpython \
  --require-runtime pypy --require-runtime node --require-runtime java \
  --timeout-ms 20000
```

Use fresh output directories. Omit optional runtime-presence requirements for a
local partial run; its missing runtimes remain visible as skips. CI pins its qualification runner to Ubuntu 24.04 and requires all four runtimes.
Pinned setup actions select cached PyPy 7.3.19/Python 3.10 and CPython 3.12; the
harness runs under CPython. Java uses the runner's explicit `JAVA_HOME_17_X64`
installation. The job verifies the preinstalled compiler, CMake, patch and Node
instead of refreshing OS packages. Missing required tools fail immediately;
setup steps have deadlines. Actual runtime versions remain in each report. SIMD baseline coverage is required;
AVX2 and AVX-512 execution depends on that runner's CPU and OS feature mask.
Running a runtime with JIT enabled does not prove a particular hot function was
compiled. The separately asserted generated-code fixture in `tests/smoke.py`
provides a smaller, deterministic check that executed JIT bytes are counted.

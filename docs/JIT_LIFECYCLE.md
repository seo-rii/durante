# Generated-code lifecycle and replay qualification

Two strict matrices compare native execution, release/debug engine-only controls,
release/debug counters with REP off/on, and the Rust supervisor. Each has 48
observations: 18 functional controls and 30 counter cases. Every scheduled case
must pass; duplicated counts, invalid/missing/truncated records, diagnostics,
changed inputs or failed cleanup are errors. No known-overcount exception remains.

## Lifecycle matrix

`tests/jit_lifecycle.py` installs a function returning 42, executes it ten times,
replaces it with a function returning 43, then executes the replacement ten times.
A stale cached function fails the output oracle. The modes are RW→RX permission
transitions, explicit unmap/remap at the same owned address, and rewriting a
previously executed RWX page. Each replacement has either zero or 32 extra NOPs.

An independent `objdump` count of the static success path, fixed loop
multiplicities and generated bodies determines the instruction oracle. The REP
copies have known byte lengths. The small RWX case requires 169 instructions,
12 requested REP bytes and two REP entries; the larger requires 489/44/2.
The old duplicated results (172/18/3 and 492/82/3) now fail unconditionally.

## Replay matrix

`tests/jit_replay.py` covers earlier executed work, partially completed writes,
inline sandbox exits and application-register preservation. Its assembly source
is fixed; static disassembly and explicit loop/generated-body multiplicities
produce independent instruction expectations.

| Fixture | Instructions | REP bytes / entries when enabled |
| --- | ---: | ---: |
| Scalar write to other cached code | 40 | 6 / 1 |
| Write to an earlier instruction in the current block | 38 | 13 / 1 |
| Forward MOVSB across RW→cached-RWX page boundary | 48 | 16 / 2 |
| Backward MOVSB across the boundary with DF set | 49 | 16 / 2 |
| STOSB into data sharing a cached code page | 51 | 16 / 2 |
| Twelve alternating rewrites across the adaptive threshold | 211 | 78 / 13 |

The boundary cases complete four iterations before the protected page faults.
Their second REP still requests ten bytes once, even though RCX is six on retry.
The self-modification fixture changes an earlier MOV immediate after that MOV
has executed: its first call must return 42, and its next call must return 43.
This caught an engine-only bug that exposed the RIP-relative write's scratch RAX
instead of the original application value. The prepared runtime reconstructs
the application registers before flushing code and uses that copy only when
dispatching a retry. A retry inside the existing cache retains its raw registers.

## Runtime and accounting contract

The [private replay patch](../toolkit/patches/dynamorio-code-replay.patch) extends
the pinned runtime, with declarations in [durante_runtime.h](../client/durante_runtime.h).
The client requires ABI version 1 through strong symbol references; an old/stock
core cannot silently run this counter without notifications. Rebuild SDK, client
and CLI together. The SDK manifest records `code_replay_patch_sha256`, and isolate
requires its reviewed value in addition to the existing source/lifetime patch.

The runtime reports only actual modified-code dispatch re-entry, distinguishing:

- **RETRY:** the faulting write will run again; a REP may have partially completed.
- **AFTER_WRITE:** the inline sandbox completed the write and exits to its successor.
- **ENTRY:** a sandbox validation/threshold check exits before client/app instructions.
- **UNKNOWN:** the runtime cannot establish the required recovery state.

Before a memory write, bounded thread-local state records its application PC and
logical block suffix. RETRY removes that unexecuted suffix from the old entry
charge; the new entry charges its own instructions. AFTER_WRITE retains the
completed write and removes only its tail. ENTRY has no charge to undo. REP
retries preserve the original demand and suppress exactly the resumed observation,
including a second internal fault. A genuine later loop iteration is counted anew.

Signals can overwrite a checkpoint even when their handlers use the same PC.
Kernel transfers therefore clear checkpoints; an interrupted pending retry is
invalid. Unknown/mismatched recovery, unsupported emulation checkpoints, missing
thread state, underflow and existing overflow remain invalid through aggregation,
exec and vfork child snapshots. An unrelated earlier signal does not by itself
invalidate later code replacement. No broad restore-state query heuristic is used.

The event does not expose or alter an application context. Its callback performs
bounded TLS/atomic bookkeeping without allocation, locks or I/O. Runtime patch
hashes are installation consistency checks, not hostile-host attestation.

## Reproduce

Build the pinned SDK, client and release CLI as described in the README, then:

```sh
python3 tests/jit_lifecycle.py --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 \
  --client build/libdurante.so --runner-bin target/release/durante \
  --output-dir .results/jit-lifecycle --require-qualified-mode rwx-rewrite
python3 tests/jit_replay.py --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 \
  --client build/libdurante.so --runner-bin target/release/durante \
  --output-dir .results/jit-replay
```

Use fresh output directories, Linux x86-64, Python 3.11+, GCC, binutils `objdump`
and `prlimit`. Setup and each invocation have wall/CPU/address-space/output bounds;
a subreaper owns adopted descendants. Sources, selected tools, runtime, client,
CLI and generated fixtures are identified before/after execution. Raw records,
commands, expectations and errors remain in `results.json`. Exit 0 requires every
row, identity and cleanup check to pass. The retained `--require-qualified-mode`
option can state an explicit requirement; the entire default matrix is strict.

Debug selection precedes forwarded engine options. Direct engine invocations
use the release default critical/error stderr mask (`12`) to suppress debug
information banners. The fixed direct debug controls classify the exact single
line `<Unknown processor cache size>` as `cpu_cache_size_unknown`: the pinned
core's `proc_set_cache_size` cannot name some hosts' CPUID cache sizes and leaves
its cache-size enum unknown. It does not change the instruction-count oracle.
Raw stderr remains in every row and `stderr.txt`; the summary reports
`cpu_cache_notices`. Repeated, truncated, additional or different diagnostics
still fail, as does this notice from a native, release or supervisor execution.
Normal exit, exact output, instruction/REP counts, input identities and cleanup
remain mandatory. This exception applies only to these fixed test fixtures,
not arbitrary target stderr or production report acceptance.

The actual client state machine also has bounded C tests with small host stubs:

```sh
DURANTE_TEST_SDK=toolkit/sdk/DynamoRIO-Linux-11.3.0-1 \
  python3 -m unittest discover -s tests -p test_client_replay.py -v
```

These include production `client/durante.c`, covering repeated partial retries,
wrong checkpoints/REP descriptors, signal aliasing, overflow, emulation and
exec/vfork publication. They use SDK headers and GCC, not a loaded runtime, and
complement the native matrices. Without the default SDK headers they explicitly
skip; an explicitly configured invalid SDK fails.

These single-threaded fixtures do not qualify concurrent code patching, runtime
GC/deoptimization, a particular JVM/Node compilation, or `isolate` execution.
Counts remain a diagnostic BB-entry metric, including its separate application
signal limitations, with application-mutable reports and no CPU-time conversion.

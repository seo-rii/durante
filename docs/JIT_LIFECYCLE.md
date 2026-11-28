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

## Protection changes under concurrent execution

`tests/exec_area_race.py` runs worker threads that call a generated function
while the main thread keeps switching its page between RX and RWX without
changing the code. Each switch removes the page's executable area, often before
any worker has executed from the new one. Such an area is removed without the
flush synchronization, and the original runtime kept using its stale pointer
when a thread's lookup of it failed after a lock upgrade: the engine and
supervisor crashed in 17 of 20 cases. The prepared runtime clears the pointer.
The harness runs a native control, the engine with the release core and the
supervisor; every call must return the expected value. The debug core is not
run: it first trips a separate consistency check for code that becomes writable
while another thread executes it, which this fix does not address.

## Code patched in place

`tests/selfmod_patch_race.py` runs worker threads that call a generated function
while the main thread keeps patching it on an RWX page, with a version counter
that tells each call which result is current. The runtime sandboxes such a page:
every block checks on entry that the code still matches a copy stored with the
block. The original runtime took that copy from the page after decoding the
block, so a patch in between gave a block whose copy was newer than its body; it
passed its check and returned the old result until the next patch. The engine
with `-sandbox_writable` failed 10 of 10 cases on the release and debug cores,
with hundreds to thousands of stale calls each, and the supervisor with default
options failed 2 of 10. The prepared runtime decodes each sandboxed block from a
snapshot of its code and stores that snapshot as the copy; it passes 40 of 40
cases in ten repeats.

## Code that keeps changing under load

`tests/jit_stress.py` has a patcher thread keep changing six generated functions
while worker threads call them. Each function has its own version counter, so a
call made while its function is stable must return that version's result. Five
modes change the code, or load the process, in different ways; each runs a
native control, then the engine alone and the supervisor, with the release and
debug cores. The engine runs without traces, as the supervisor runs it.

| Mode | What it does | Runtime before this suite |
| --- | --- | --- |
| `inplace` | patches on RWX pages; one instruction crosses a page boundary | passed; a rare debug-core check, below |
| `remap` | replaces the page with a new mapping of the new code | failed 20 of 20 cases |
| `churn` | `inplace` with workers that are short-lived threads | passed |
| `signal` | `inplace` with signals whose handler also calls the functions | failed 1 of 20 cases idle, 7 of 8 release-core engine runs under load |
| `fork` | `inplace` with children forked while the code changes | passed |

The suite found five faults in the runtime, which the prepared runtime fixes:

- **A new mapping over code that has run.** `mmap` with `MAP_FIXED` over
  executed code was handled as a protection change, so the blocks built from the
  old code stayed. With the release core 17 to 34 million calls per run returned
  the old result; the debug core stopped on a consistency check. The runtime now
  flushes those blocks before the system call, and again after it for blocks
  that another thread built in between.
- **A signal in a block that is being flushed.** The handler unlinks the
  interrupted block so that the thread leaves it soon. It did so through the
  entry points that also free the thread's flushed blocks, while the thread was
  still inside one of them: the thread resumed freed code, and the runtime
  crashed on a later signal. The handler now only takes the lock that keeps a
  flusher out.
- **The pointer to that block.** The runtime kept the unlinked block to re-link
  it when the signal is delivered. A flush could free the block first, and the
  runtime then re-linked freed memory. The thread now drops the pointer before
  it frees flushed blocks.
- **More pending signals than the runtime preallocates for.** With more than 8
  signals pending on a thread, the handler allocated memory, taking locks that
  the interrupted runtime code could hold. At that limit the runtime no longer
  queues a standard signal that is already pending, which the kernel does not
  queue again either.
- **A block that wrote to its own code.** The thread deleted such a block while a
  flusher on another thread could be unlinking the same block and allocating
  from the thread's heap. The debug core reported heap corruption or a missing
  link in about 1 of 20 `inplace` runs when the host was busy; with the window
  widened by a delay, 6 of 40 runs failed, and 0 of 40 with the fix. The thread
  now holds its linking lock while it deletes.
- **A protection change that has not finished.** The `wx` mode keeps the pages
  RX and makes them RWX only around each patch, as a JIT that keeps its code
  W^X does. The runtime updates its view of the code before the `mprotect` call.
  A thread that built a block from the page in between read the old, read-only
  protection, so the runtime took the code for read-only: it neither kept the
  page write-protected nor checked the block's code on entry. The JIT's next
  write then went unnoticed and the old code kept running: under load the
  release core returned one stale call in 3 of 8 runs. The runtime now records the change from before the system call
  until after it, and checks the code of blocks built from the page meanwhile.
  The debug core also stopped on a list check in 6 of 10 cases. That check
  failed on blocks that one flush had moved onto an area which a second flush
  then reached; the runtime now skips it for an area already being freed.

With the prepared runtime the five modes pass 40 of 40 cases in two repeats.
With four busy threads on the same machine, every mode passes 8 of 8 engine runs
with each core.

## Writes to a large code cache

A JIT keeps its code in one large writable and executable mapping: HotSpot's
code cache holds the template interpreter, stubs and compiled methods. The
original runtime made the whole mapping read-only once code ran from it, and
merged its executable areas, so every write to any of its pages flushed every
block built from it. A new method next to running code, or an inline-cache
update, threw away the interpreter and all compiled code, which then had to be
rebuilt as it ran again. An OpenJDK 21 hello world under the engine with the
counter flushed its 624-page (2.5 MiB) code cache 2,452 times. It built 134,816
blocks, of which 83,505 were distinct, and changed the protection of 3.1 million
pages.

The prepared runtime tracks code in writable memory one page at a time
(`-page_granular_writable_code`, on by default here). A new executable area in
writable memory covers only its page, and such areas are not merged. A write
then flushes only the blocks of the pages it writes. On a contest `bfs_grid`
run, write faults fell to 244 and flushed blocks to 2,037, and 168,534 of the
170,145 blocks built were distinct.

CPU seconds on one pinned CPU of a 4-vCPU Xeon guest, median of 3 interleaved
runs, with the counter as the supervisor runs it. Java uses the corpus flags
with compressed class pointers disabled (the flags before CDS was kept):

| Program | native | whole-region | per page |
|---|---:|---:|---:|
| hello world | 0.08 | 2.91 | 1.45 |
| compute loop (1.75 s native) | 1.75 | 5.64 | 4.01 |
| `bfs_grid` | 0.35 | 7.02 | 3.53 |
| `dijkstra` | 0.33 | 7.47 | 3.41 |
| `lcs` | 0.34 | 4.35 | 2.51 |
| `fenwick` | 0.42 | 6.80 | 4.13 |
| `kmp` | 0.81 | 7.56 | 3.92 |
| `sort_bs` | 0.57 | 7.47 | 4.56 |
| `deep_dfs` | 0.60 | 7.64 | 3.84 |

With class data sharing kept as well (`-XX:CompressedClassSpaceSize=64m`, the
corpus's `java-cds` cells and aonohako's `java21_cds` profile), the geometric
mean over the seven problems fell from 6.80 s with the whole-region runtime and
the default flags to 3.14 s (14.7x to 6.8x the default flags' native time).

Node, PyPy, PHP's tracing JIT and CPython changed within noise on `bfs_grid`,
`dijkstra` and `sort_bs` (PyPy `dijkstra` fell from 3.57 to 2.94 s). Peak RSS
of the Java runs is unchanged.

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
The client requires ABI version 2 through strong symbol references; an old/stock
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

Both lifecycle and replay matrices share the same diagnostic classification;
the replay harness also records the imported classifier's source identity.
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

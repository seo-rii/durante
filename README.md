# Workmeter

Workmeter records software work performed by a Linux x86-64 process tree under
DynamoRIO. It counts application instructions at basic-block entry and optionally
records REP MOVS/STOS requested bytes. It follows ordinary forks, Linux vfork /
posix_spawn paths and exec image changes, then sums complete process/image records
without charging inherited parent counters again.

This is **diagnostic measurement**, not CPU milliseconds, exact hardware-retired
instructions, a memory limit, or a trusted security boundary. Its counters share
the measured program's address space. A caller must retain its own sandbox,
resource limits, output validation and complete-tree cleanup.

## Build and test

The pinned SDK is DynamoRIO 11.3.0-1 (source revision
`6847bb210bea0ca11d1bf47704383d6a075da490`). The fetcher verifies its release
archive SHA-256 before extracting the required build/runtime subset.

```sh
python3 toolkit/fetch_sdk.py
cmake -S client -B build -DCMAKE_BUILD_TYPE=Release \
  -DDynamoRIO_DIR="$PWD/toolkit/sdk/DynamoRIO-Linux-11.3.0-1/cmake"
cmake --build build -j2
python3 -m unittest discover -s tests -v
python3 tests/smoke.py \
  --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 \
  --client build/libworkmeter.so --output-dir .results/smoke
python3 tests/smoke.py \
  --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 \
  --client build/libworkmeter.so --compact-vm --output-dir .results/smoke-compact
```

`--compact-vm` verifies the same suite with DynamoRIO `-vm_size 32M
-vmheap_size 32M`, the bounded reservation configuration used by aonohako. This
setting reduces address-space reservations; it does not waive target limits.

The build requires GCC or Clang, CMake and Linux x86-64. Tests additionally use
Python 3 and GCC; PyPy is tested when installed, and absence is reported as a
coverage gap. No SDK or generated executable is committed.

## Integrate

A trusted launcher creates a fresh, empty, target-writable private directory and
launches the original executable with its existing arguments and environment:

```sh
toolkit/sdk/DynamoRIO-Linux-11.3.0-1/bin64/drrun \
  -quiet -follow_children -c "$PWD/build/libworkmeter.so" \
  -outdir /absolute/private/new-directory -rep_bytes -- /path/to/program args
```

Track that launched process's PID. After it **and all descendants** have exited
and been reaped, collect the records:

```sh
python3 collect.py --dir /absolute/private/new-directory --root-pid TARGET_PID
```

The standard-library Python collector is a reference implementation. An embedding
backend can implement the same [bounded protocol](docs/PROTOCOL.md), avoiding a
Python runtime dependency in its execution sandbox. Shared
[protocol vectors](tests/protocol_vectors.json) exercise complete and incomplete
trees, exec boundaries, duplicate records and overflow handling.

The client does not capture or replace target stdout/stderr, set its exit status,
change fork to another syscall, or weaken sandbox policies. It writes separate
lifecycle records. The caller must put resource bounds around execution and
cleanup; the collector neither waits for processes nor kills them. A live or
truncated tree is unavailable, never a valid partial total.

## Coverage and limits

The native smoke suite verifies known instruction counts, exact child-work
increments, parent-prefix nonduplication, thread aggregation, nested forks,
fork/exec, exec chains, failed exec, empty-environment exec, vfork immediate exit,
vfork failed exec, posix_spawn from several parent threads, signals and killed
children. It also checks generated JIT code and REP widths / byte demand.

Linux vfork and `CLONE_VM | CLONE_VFORK` children share client globals before exec.
Their counters and stream state therefore live in the child's separate
DynamoRIO thread context and never enter the parent's thread sum. File descriptors
are closed in the child before exec/exit; an old child context may be destroyed in
the parent after exec and must not close a reused parent descriptor number.

Non-thread `CLONE_VM` without `CLONE_VFORK`, shared-file-table process clones,
PID reuse within one collection, nested process creation before a vfork child's
exec, and successful exec from a multithreaded process remain unsupported or
incomplete. The collector rejects their accounting rather than silently omitting
work. Ordinary thread creation is aggregated separately from process creation.

Python `subprocess.run(["/bin/true"])` with its default descriptor-closing path
timed out in a local diagnostic. Ordinary Python execution and the dedicated
fork/vfork/posix_spawn fixtures do not establish support for this path. Python
subprocess creation remains unqualified; the collector rejects its incomplete
records.

Observed signals are reported as `quality: signals_observed`. A whole basic block
is charged at entry, so signal delivery can charge an unexecuted tail. This is
explicitly a basic-block-entry work metric. A killed process lacks a final record
and invalidates the tree. Nonzero **normal** exits can have complete work records;
the caller still applies its original execution/verdict policy.

CPython, native Go/Rust and selected Node/shell fixtures have separate compatibility
checks in the embedding project's qualification. They are not universal language
certification. In particular, Java 17 currently crashes during JVM startup with
this counter while the engine-only control succeeds; JVM use is unqualified.
Generated-code unit tests do not establish JVM compatibility.

Instruction work and REP bytes have separate units. Kernel work is excluded;
SIMD and scalar instructions have different costs, and CPU-specific library
implementations can perform the same task with radically different counts. No
weights, virtual milliseconds or production TLE conversion are defined here.
Parent/child instruction totals may be added; RSS peaks must not be added or
subtracted as if they represented simultaneously resident memory.

The first integration adds overflow detection and process-tree lifecycle tracking
beyond the earlier single-process experiment. Its overhead must be measured on
this exact build; earlier single-process overhead numbers do not qualify it.

## Upstream and license

The counter is derived from DynamoRIO's BSD-licensed `inscount.cpp` and
`countcalls.c` examples. Original notices are retained in the client source and
[LICENSE](LICENSE). The SDK is distributed separately under its upstream license.

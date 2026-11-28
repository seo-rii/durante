# Security boundary

Durante 0.1 is a diagnostic instruction-work counter and process supervisor.
`durante run` requires external execution isolation; `durante isolate` adds the
experimental namespace profile described below. Neither provides a trusted
timing source or an adversarially tamper-proof meter. A counter report's `valid`
and `complete` describe structural consistency and observed lifecycle completion;
they do not attest counter integrity. Every report retains
`authority: diagnostic_only`. Both execution commands report
`security.counter_integrity: application_mutable`. The `run` command reports
`security.isolation: external_required`; `isolate` reports its
`bubblewrap-0.12-durante-pid1-v1` profile instead.

## Confirmed integrity limitation

The client and counters live inside the measured application's address space.
The application can also access records written under its own UID. A bounded
native regression demonstrates a child rewriting the exited root's instruction
count to one before collection. The result can be structurally complete even
though the root performed a million-iteration loop. Protected DynamoRIO
descriptors prevent ordinary descriptor cleanup from breaking the client; they
do not make its memory or files inaccessible to an adversarial application.

This cannot be fixed by schema validation, a private temporary directory,
checksums, or a signing key stored in the same address space. A fully trusted
charge still needs a measurement backend outside the submission's mutable trust
domain. Until that exists, do not use these counts, a weighted conversion, or a
normal CLI exit status to authorize AC/TLE, billing, or security decisions
unless the optional in-process isolation below is enabled and its receipt is
validated.

## Optional in-process meter isolation

`durante run --protect-meter` (client `-protect_meter`) enables software fault
isolation of the meter against the measured application, without a hardware PMU
or an out-of-process backend. The report of such a run sets
`meter_protection_requested: true`. Only a valid and complete terminal
measurement whose images all carry the client's receipt also gets
`security.counter_integrity: protected_in_process_v1` and a
`meter_protection: {version: 1}` receipt at the top level; the runner rejects a
tree whose images do not all carry the receipt. A launch failure, an incomplete
or invalid measurement, or an integrity violation keeps
`counter_integrity: application_mutable` and has no top-level receipt, and
`authority` stays `diagnostic_only` in every case. A detected attempt to change the counts is a
sticky, fail-closed `integrity_violation`: the live page is invalidated, a
record names the vector, the process exits before the attempt takes effect, and
the measurement is invalid with no usable totals. This is still not a
hardware-attested count, and it depends on the prepared runtime (below); treat
it as the strongest available in-process guarantee, not a trusted enclave.

**Protected ranges.** The prepared runtime (`-durante_window`, set by the runner
to one 4 GiB-aligned window) reserves the whole window `PROT_NONE` before the
application image is mapped and places every writable runtime and client
allocation inside it: the relocated `libdynamorio.so` image and its writable
segments, the code cache, all DR heap (so per-thread `thread_data_t`, the raw
TLS slots, the client globals and the boundary stacks), and the `-live_work_v1`
page. Nothing of the application is ever inside the window, and no runtime
allocation falls back outside it; the client audits the whole address space
against this invariant before the first application instruction. The first and
last 16 MiB of the window stay `PROT_NONE` as guard regions. The per-process
record files and the live page are additionally protected by inode, device and
path checks, and the final report by the device and inode the runner passes with
`-protect_file`.

**What it stops.** Every application store is range-checked before it executes
with a flag- and register-preserving inline sequence (`lea; bswap; mov ecx,ecx;
lea; jrcxz`); stores whose address cannot be formed inline (segment-relative
with a clashing register, register bit offsets, AVX-512 scatter) take a checked
callback, and stores that cannot be decoded or that change FS/GS, plus exotic
forms such as `movdir64b`/`enqcmd`, are denied. System calls are allow-listed:
output buffers, kernel-written user pointers (`set_tid_address`, clone
TID/pidfd, `rseq`, `set_robust_list`), mapping changes (`mmap MAP_FIXED`,
`munmap`, `mremap`, `mprotect`, `madvise`, `brk`) and signal stacks are checked
against the window; `ptrace`, `process_vm_writev`, `io_uring`, `userfaultfd`,
`perf_event_open`, `bpf`, `seccomp` and opens of the record files or
`/proc/*/mem` are refused; syscalls that cannot be checked fail with `EPERM`.
Default mode (without the flag) is unchanged and emits byte-identical
instrumentation. Without the flag, the stock runtime refuses `mmap MAP_FIXED`
and `mprotect` over its own memory for a dynamically linked program, but it lets
a static program `mprotect` the client's data: the client is then loaded at its
preferred low address. Only `-protect_meter` stops that. See `tests/meter_integrity.py` for the attack suite and
`client/meter_protect.h` for the full design.

Native CPU-feature restrictions use the same in-process DynamoRIO client.
They stop an observed forbidden instruction before it executes, including code
translated after startup, but do not make the client, code cache or lifecycle
records inaccessible to hostile application code. Treat the policy receipt and
violation reason as application-mutable diagnostics. The caller still supplies
the existing execution sandbox and resource owner. Exit 126 alone is not a
policy violation; consumers must validate the policy metadata and lifecycle
event. See the [native policy guide](https://github.com/seo-rii/durante/blob/main/docs/CPU_FEATURE_POLICY.md).

## Execution isolation

`durante run` follows and reaps owned descendants, including descendants that
change process groups. It does not provide filesystem, network or syscall
isolation, or aggregate resource limits. Same-UID code can still modify
accessible files or signal the supervisor, including SIGSTOP/SIGKILL. A target
can clear its parent-death signal. Parent death and descendant ownership are not
a kernel-enforced containment boundary.

For untrusted code, an external sandbox must supply distinct credentials and
capability dropping, protected output/workspace mounts, filesystem and network
policy, an explicit environment allowlist with no host credentials, signal/PID
isolation, CPU/RSS/process/storage limits and a separately
owned watchdog that kills the entire kernel-controlled execution group. An
in-process counter remains mutable even with that execution sandbox.

The optional `durante isolate` profile supplies the documented namespace, mount,
capability and syscall restrictions. It still requires an external owner to
provide the bounded cgroup environment, aggregate lifetime and disk/I/O limits,
and teardown after controller death. Its output quota is enforced only when
bounded capture is enabled; otherwise the owner must enforce it. See
[ISOLATION.md](docs/ISOLATION.md) for the complete prerequisites and tested scope.

## Supervisor hardening

- The dedicated supervisor stays non-dumpable until exit, blocking ordinary
  same-UID `/proc` descriptor/memory access and ptrace attachment. Host
  administrators and already privileged tracers remain outside this boundary.
- The target starts with `no_new_privs`; setuid/file-capability execs cannot
  grant it new privileges. Only stdin/stdout/stderr survive exec. Other inherited
  descriptors are marked close-on-exec in the child. The CLI requires kernel
  support for `close_range(CLOSE_RANGE_CLOEXEC)` and refuses launch if unavailable.
- Final report publication rejects replaced paths, symlinks, hardlink aliases,
  permission broadening and detectable pre-existing writes or trailing data.
  Reports must still be stored in an externally protected directory: this does
  not prevent all same-UID in-place changes or modification after CLI exit.
- Reaping and kill passes have bounded operation budgets. A busy child producer
  cannot monopolize one userspace reap loop indefinitely. External limits are
  still necessary to prevent process or kernel-resource exhaustion.
- Automatic record cleanup examines at most the protocol's process-file bound,
  uses a pinned directory descriptor for unlinking and never recursively walks
  target-created directories. Unexpected contents are retained with
  `cleanup_failed`, no usable totals and an explicit retained path.

## Parser and native regression coverage

Rust and Python collectors enforce bounded files, total bytes, lines, events and
processes. Flat scalar JSON is required; duplicate keys, nested structures,
invalid encodings/types, impossible REP telemetry, inconsistent process graphs
and integer overflow fail closed without aggregate counters. Correctly rounded
JSON floats and explicit integer normalization keep the reference and native
decoders consistent at negative-zero and overflow boundaries; counter fields
still require integers. Filesystem checks
reject symlinks, hardlinks, FIFOs and nonregular records. Client stream opens
are nonblocking before validating inode type, preventing a replaced FIFO from
stalling instrumentation on exec.

`tests/runner_security.rs` exercises supervisor access, report replacement and
extension, inherited descriptors, no-new-privileges, bounded churn and hostile
cleanup. `tests/adversarial_native.py` checks normal execution, FIFO/symlink/
hardlink replacement and file/memfd `execveat`. The latter exec paths currently
have incomplete instrumentation and are rejected as unavailable measurements
when CPU restrictions are disabled; they are not certified as supported paths.
When a CPU policy is enabled, all application `execve` and `execveat` transitions
return `EPERM` before execution. A normal pathname alone cannot qualify the
replacement: null argument vectors and 32-bit images can also escape client
reinjection. Policy guards therefore do not depend on pathname or ELF-header
inspection. Its optional
`--demonstrate-report-forgery` case records the known integrity counterexample
separately from attacks prevented.

The client only accounts for the native Linux x86-64 syscall ABI. An executed
`int 0x80` fork previously created a child while the result incorrectly reported
one complete process. Runtime guards now invalidate executed compatibility
syscall instructions and x32 syscall identifiers, even when REP telemetry is
disabled. With CPU restrictions disabled, the original syscall and its result
are preserved; unsupported ABI
execution yields no usable aggregate. Merely containing unreachable compatibility
bytes does not invalidate a run. `tests/compat_syscalls.py` compares 18 bounded
cases against native controls, including ordinary ENOSYS calls. Actual x32
execution remains unqualified on hosts without that kernel ABI.

With a CPU policy enabled, compatibility syscall instructions stop before the
instruction and x32 calls return `EPERM` before reaching the kernel. Unsupported
clone/spawn forms are also denied before a child can escape instrumentation.
The native policy fixtures verify actual output and memory effects, including
continued parent instrumentation after a shared-VM child is stopped.

The [generated-code checks](docs/JIT_LIFECYCLE.md) require exact instruction and
REP totals through RWX replacement, partial REP retries and inline self-modifying
code. The prepared runtime reports actual dispatch re-entry, distinguishes a
faulting write from a completed write and a pre-entry check, and restores the
application registers before a fault retry. Thread-local checkpoints correct
only the unexecuted suffix and retain the original REP demand once. Unknown or
ambiguous replay, lost checkpoints, interrupted pending retries and arithmetic
errors invalidate the measurement, including thread/exec/vfork snapshots.
The client requires the private runtime ABI; no broad restore-state heuristic
or accepted overcount signature remains. These are fixed diagnostic fixtures,
not a guarantee for concurrent code patching or protection from report forgery.

## Fuzzing

The committed targets call the production decoder and lifecycle aggregator.
`raw_stream` mutates PID-prefixed raw JSONL, including malformed UTF-8 and
framing. `lifecycle_tree` generates and mutates fork/exec graphs, ordering,
duplicates, types and overflow boundaries. Assertions check that invalid results
never expose totals and accepted segment/process/tree sums agree; ordering and
duplicate-stream metamorphisms exercise aggregation invariants.
`filesystem` also calls the production path-based collector on exclusively owned
temporary fixtures: regular records, FIFOs, symlinks, hardlinks, directories,
sparse oversized files, malformed names and symlinked roots. A valid sentinel
ensures that accidentally following a link cannot be masked by invalid JSON;
sentinel contents must remain unchanged and unsafe inode types must be rejected.

```sh
rustup toolchain install nightly-2026-04-22 --profile minimal
cargo +nightly-2026-04-22 install cargo-fuzz --version 0.13.2 --locked
cargo +nightly-2026-04-22 fuzz run raw_stream -- -max_total_time=60 -max_len=65536 -timeout=3 -rss_limit_mb=1024
cargo +nightly-2026-04-22 fuzz run lifecycle_tree -- -max_total_time=60 -max_len=65536 -timeout=3 -rss_limit_mb=1024
cargo +nightly-2026-04-22 fuzz run filesystem -- -max_total_time=60 -max_len=4096 -timeout=3 -rss_limit_mb=1024
```

CI runs all three with AddressSanitizer and retains failing inputs. A successful
bounded campaign is coverage evidence, not proof of absence of vulnerabilities;
native engine code, concurrent filesystem races and OS containment require
separate validation. Generated corpora, build outputs and crash inputs are local
artifacts rather than committed source; the small fixed seed corpus is committed.

`tests/differential_collect.py --collector-bin target/release/durante --cases 512
--seed 20261111` also mutates the same temporary records for the production Rust
CLI and Python reference. It checks matching validity, exact complete results and
absence of totals on rejection. Structured numeric boundaries are mixed with raw
framing/encoding mutations so malformed graphs cannot mask scalar-parser
differences. CI runs this bounded cross-language check before building the SDK.
On disagreement or collector failure it retains the exact raw records, root PID,
both output streams and tool identities in a private, size-bounded bundle. See
[differential fuzz replay](docs/DIFFERENTIAL_FUZZING.md) for standalone replay,
artifact bounds and the limits of this diagnostic evidence.

## Optional namespace profile

`durante isolate` adds an outer watchdog and a patched, non-dumpable namespace
PID 1, with read-only inputs and a bounded capability/syscall profile. It requires
an externally owned, finite cgroup v2 execution environment and output limits.
Its nested counter report remains explicitly untrusted. See
[ISOLATION.md](docs/ISOLATION.md) for prerequisites, compatibility restrictions and
remaining host/kernel and forced-controller-death responsibilities.

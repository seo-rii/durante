# Security boundary

Durante 0.1 is a diagnostic instruction-work counter and process supervisor.
It is not an execution sandbox, a trusted timing source, or an adversarially
tamper-proof meter. A report's `valid` and `complete` describe structural
consistency and observed lifecycle completion; they do not attest counter
integrity. Every report retains `authority: diagnostic_only`. The CLI additionally
reports `security.counter_integrity: application_mutable` and
`security.isolation: external_required`.

## Confirmed integrity limitation

The client and counters live inside the measured application's address space.
The application can also access records written under its own UID. A bounded
native regression demonstrates a child rewriting the exited root's instruction
count to one before collection. The result can be structurally complete even
though the root performed a million-iteration loop. Protected DynamoRIO
descriptors prevent ordinary descriptor cleanup from breaking the client; they
do not make its memory or files inaccessible to an adversarial application.

This cannot be fixed by schema validation, a private temporary directory,
checksums, or a signing key stored in the same address space. Trusted charging
needs a measurement backend outside the submission's mutable trust domain.
Until that exists, do not use these counts, a weighted conversion, or a normal
CLI exit status to authorize AC/TLE, billing, or security decisions.

## Execution isolation

The CLI follows and reaps owned descendants, including descendants that change
process groups. It does not restrict filesystem reads/writes, network access,
syscalls, inherited capabilities, process creation, or resource consumption.
Same-UID code can still modify accessible files or signal the supervisor,
including SIGSTOP/SIGKILL. A target can clear its parent-death signal. Parent
death and descendant ownership are not a kernel-enforced containment boundary.

For untrusted code, an external sandbox must supply distinct credentials and
capability dropping, protected output/workspace mounts, filesystem and network
policy, signal/PID isolation, CPU/RSS/process/storage limits and a separately
owned watchdog that kills the entire kernel-controlled execution group. An
in-process counter remains mutable even with that execution sandbox.

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
and integer overflow fail closed without aggregate counters. Filesystem checks
reject symlinks, hardlinks, FIFOs and nonregular records. Client stream opens
are nonblocking before validating inode type, preventing a replaced FIFO from
stalling instrumentation on exec.

`tests/runner_security.rs` exercises supervisor access, report replacement and
extension, inherited descriptors, no-new-privileges, bounded churn and hostile
cleanup. `tests/adversarial_native.py` checks normal execution, FIFO/symlink/
hardlink replacement and file/memfd `execveat`. The latter exec paths currently
have incomplete instrumentation and are rejected as unavailable measurements;
they are not certified as supported paths. Its optional
`--demonstrate-report-forgery` case records the known integrity counterexample
separately from attacks prevented.

## Fuzzing

The committed targets call the production decoder and lifecycle aggregator.
`raw_stream` mutates PID-prefixed raw JSONL, including malformed UTF-8 and
framing. `lifecycle_tree` generates and mutates fork/exec graphs, ordering,
duplicates, types and overflow boundaries. Assertions check that invalid results
never expose totals and accepted segment/process/tree sums agree; ordering and
duplicate-stream metamorphisms exercise aggregation invariants.

```sh
rustup toolchain install nightly-2026-04-22 --profile minimal
cargo +nightly-2026-04-22 install cargo-fuzz --version 0.13.2 --locked
cargo +nightly-2026-04-22 fuzz run raw_stream -- -max_total_time=60 -max_len=65536 -timeout=3 -rss_limit_mb=1024
cargo +nightly-2026-04-22 fuzz run lifecycle_tree -- -max_total_time=60 -max_len=65536 -timeout=3 -rss_limit_mb=1024
```

CI runs both with AddressSanitizer and retains failing inputs. A successful
bounded campaign is coverage evidence, not proof of absence of vulnerabilities;
native engine code, concurrent filesystem races and OS containment require
separate validation. Generated corpora, build outputs and crash inputs are local
artifacts rather than committed source.

# Java safepoint recurrence and context evidence

BUG-006 is an open JVM compatibility failure. The retained Java 17 CI observation
aborted with `SharedRuntime::get_poll_stub` and the guarantee `safepoint polling:
pc must refer to an nmethod`. It used the engine-only basic-block profile, without
the Durante client. Its stack was in `ModuleBootstrap` / `System.initPhase2`,
before the workload's child-launch code. Naming the workload `POSIX_SPAWN` does
not establish that process creation caused the abort.

The observed JVM was Temurin 17.0.21+9 with Serial GC, a 64-MiB heap, 256-KiB thread
stacks, 32-MiB compressed-class/code spaces and two active processors. The first
mixed workload matrix passed 39/40 observations; its unchanged rerun passed
40/40. Those totals are not a repeated same-case failure rate. A successful rerun
is non-reproduction, not a fix. The production runtime is unchanged by this tool.

## Fixed recurrence campaign

`tests/jvm_safepoint.py` repeats the existing Java `RuntimeWorkload processes`
fixture with its `POSIX_SPAWN` flag and unchanged checksum/child-exit oracle. Every
round includes native, engine-only, client REP-off and the production supervisor.
The latter two retain the existing independent process/image and counter checks.
There are no expected-failure exceptions and no replacement retries.

```sh
python3 tests/jvm_safepoint.py \
  --sdk /prepared/sdk --client /prepared/libdurante.so \
  --runner-bin /prepared/durante \
  --java /prepared/temurin-17.0.21+9/bin/java \
  --javac /prepared/temurin-17.0.21+9/bin/javac \
  --java-major 17 --repeats 5 --output-dir /private/fresh-recurrence
```

Supply an explicit JDK and preserve its distribution/version. Java 21 is also
accepted as a separately labeled control. A different provider, host, image or
CPU placement must not be presented as the original CI environment.

The fixed maximum is five rounds / twenty observations and a 600-second campaign
deadline. Processes inherit the first two allowed CPUs. Every workload keeps the
existing 2-GiB address-space, 90-second CPU, 1-MiB file, zero-core and 120-second
wall bounds; the supervisor has up to five additional seconds for publication.
A new row starts only when the full bound plus ten seconds for collection and
inspection and eight seconds for termination/cleanup fit the remaining campaign
budget. Otherwise it and every subsequent row stay unrun. The campaign never
shortens an observation's profile to use the last few seconds. These are per-process
limits, not an aggregate cgroup budget. Run the campaign without concurrent
benchmark activity, under an external execution owner when aggregate bounds are
needed.

The output directory must be fresh. `results.json` retains the entire planned
matrix, actual commands, captured stdout/stderr, raw reports, fatal-log paths,
runtime versions, CPU placement, before/after input identities and cleanup state.
The prepared SDK is checked against the current production source and both
runtime-patch hashes. A failed native control or incomplete cleanup blocks later
rows explicitly; a deadline leaves the remaining rows incomplete. Exit 0 requires
every planned row to complete and pass with unchanged inputs and cleanup. Any
failure or unrun row yields exit 1. Exit 0 does not close BUG-006 or qualify every
JVM configuration.

Fatal reports are inspected with count/byte bounds and retained verbatim. The
classifier requires the safepoint guarantee, `sharedRuntime.cpp` source location
and `get_poll_stub` frame. Other JVM failures, other process signals, timeouts and
cleanup failures remain distinct. Fatal-log PIDs distinguish root from child
failures, including the supervisor's separately recorded target PID.

## Separate debugger context capture

`hs_err` records the fatal-handler state. Its final RIP is not the original
polling PC; a saved zero R12 is insufficient to prove failed PC translation.
Recurrence reports therefore leave `original_polling_pc` and
`original_signal_context` unavailable rather than deriving them from that
snapshot.

After preserving an unchanged recurrence campaign, an optional separate run can
observe the context handoff with GDB:

```sh
python3 tests/jvm_safepoint.py \
  --sdk /prepared/sdk --client /prepared/libdurante.so \
  --runner-bin /prepared/durante \
  --java /prepared/temurin-17.0.21+9/bin/java \
  --javac /prepared/temurin-17.0.21+9/bin/javac \
  --java-major 17 --context --gdb /usr/bin/gdb \
  --output-dir /private/fresh-context
```

This runs one native control followed by one engine-only debugger observation.
It is diagnostic only. GDB changes scheduling and installs software breakpoints;
its transcript is retained separately and bounded by the same 1-MiB file limit.
Debugger startup can still share stdout, so it cannot substitute for the
exact-output recurrence matrix. The helper keeps target ASLR and JVM/engine options unchanged,
follows the parent only, and uses exact function-entry addresses so SysV
argument-register meanings are explicit. It does not modify runtime patches,
signal masks or JVM flags to suppress an error.

The bounded `jvm-context.jsonl` keeps three different observations:

1. Debugger signal-delivery stops, with PC, registers and available siginfo. This
   is before the process handler and may point into engine or generated cache
   code; it is not automatically an application/JVM polling PC.
2. The signal and delivered `ucontext` fields at entry to HotSpot's signal handler.
   The context layout is explicitly Linux x86-64 glibc; it is not portable to
   another OS/ABI.
3. The first argument at exact `SharedRuntime::get_poll_stub` entry, when observed.

Timestamps, inferior PID and thread IDs permit correlation, without asserting
that unrelated stops belong to the same signal. An unhit breakpoint, unavailable
symbol, failed read or exhausted 256-event budget is reported. Terminal status
and probe-coverage metadata survive that event cap without raising it. Missing context
coverage is not filled from `hs_err`. The records do not themselves identify an
engine fragment or prove which translation branch executed; that requires
separate evidence. A debugger-only failure is not proof of the unchanged
runtime's cause or a correction.

## Regression coverage

`python3 -m unittest discover -s tests -p test_jvm_safepoint.py -v` checks the
fixed plan, preservation of failures before later successes, native/cleanup and
deadline stops, bounded fatal inspection, exact symptom attribution, root/child
distinction, and absent/truncated debugger coverage. Existing shared workload
tests continue to exercise the functional and accounting oracles.

## Bounded observations on 2026-11-15

The downloaded Temurin 17.0.21+9 `java`, `javac`, `libjvm`, `jspawnhelper` and
generated `RuntimeWorkload.class` hashes matched the retained CI failure's
identities. The local Linux x86-64 host and two-CPU placement remained distinct
from that original CI environment.

The initial twenty-observation campaign ran for 600.279 seconds: sixteen passed,
two timed out and two remained unrun. Its first four engine-only observations
passed without the safepoint guarantee. The initial harness incorrectly reduced
per-observation deadlines to the remaining campaign time, causing a later
supervisor deadline and a 4.8-second engine-only deadline. Those two outcomes
are harness budget effects, not BUG-006 recurrences. The failed receipt and its
exact source snapshot are preserved unchanged.

After correcting admission to require a full observation and fixed margins, a
separate one-round control passed all four modes in 141.187 seconds. Inputs
remained unchanged, all cleanup completed and CPU affinity was restored. This
is finite non-reproduction, not a production runtime correction.

The separate debugger attempt completed the native control and the JVM exited
normally, but context capture failed. The 256-event limit was reached, and the
debugger's 608,521-byte console transcript exceeded the normal 64-KiB capture
bound. Signal-stop contexts were recorded; neither a HotSpot signal-handler
entry nor a poll-stub argument was observed. The SDK auto-load warning is
retained, without changing global debugger configuration or assigning it a
cause. Original polling-PC / context-translation evidence remains unavailable.

The final tool redirects debugger chatter to a separate bounded transcript and
preserves terminal/coverage metadata even after event exhaustion. Actual GDB
configuration and recorder regressions verify these changes without increasing
either bound; the original failed attempt is not reclassified. These final
logging refinements have not established a successful JVM context capture.
Fourteen focused unit regressions pass. BUG-006 remains open and its cause is
unconfirmed; no production runtime or client patch is included.

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
It is diagnostic only. GDB changes scheduling and installs breakpoints in native
DynamoRIO code; its transcript remains bounded by the same 1-MiB file limit.
The helper retains target ASLR, JVM/engine options and parent-only following.
The prepared release core must include its matching `libdynamorio.so.debug`.
Both files are hashed, and GDB must report identical GNU build IDs with the debug
file owned by the declared core. Missing types, identities or native code
addresses fail capture rather than guessing a context layout.

When the caller supplies `LD_LIBRARY_PATH`, the harness starts GDB with that
variable unset and restores its exact value for the target through `env`
arguments. An explicitly empty value remains empty. This prevents a caller's
Python shared library from replacing GDB's system interpreter while retaining
GDB's system standard-library paths. The target keeps its original library
environment, and the additional `env` executable is included in the input
identities. No global environment or debugger configuration is changed.
Failed captures retain a bounded transcript excerpt with its byte count and
hash, so a startup error remains readable after temporary test files are removed.

The current context stream uses schema 2. On the first real signal-delivery
stop, it resolves the pinned runtime's native `translate_sigcontext` function and
checks that the exact entry lies in the declared core's executable mapping. It
records Linux x86-64 entry arguments, the typed `kernel_ucontext_t` fields before
translation, the function's typed boolean return and the same context afterward.
Call IDs, inferior PIDs, thread IDs and context addresses pair entries and
returns. A missing return or an out-of-scope native frame is an explicit failure.
A false translation return or zero resulting PC is retained as observed data;
it is not confused with a debugger read error or automatically attributed to
BUG-006. Neither context is relabeled as a HotSpot handler entry or poll-stub
argument. Those application-level observations remain unavailable.

Raw signal delivery stops include many runtime-internal faults. The debugger
continues observing them, but records only the first sixteen raw stops and
reports the total observed stop count and sampled-out count. Native translation
pairs share the original 256-event budget. Exceeding that budget remains a failed,
truncated capture. A bounded first false-return/zero-PC sample and its occurrence
count survive in the terminal summary even after exhaustion. This does not fill
missing pairs or make a truncated stream complete. Terminal exit and probe
coverage metadata also survive the cap. No signal masks, ucontexts or runtime
patches are changed by the observer.

Schema 1 streams from the earlier application-entry approach remain readable
with their original coverage semantics. They are not reclassified as native
translation evidence. Exact target output in the unmodified recurrence matrix
remains separate from debugger observations. Even a fully captured successful
run is non-reproduction, not an explanation or correction of the original abort.

## Regression coverage

`python3 -m unittest discover -s tests -p test_jvm_safepoint.py -v` checks the
fixed plan, preservation of failures before later successes, native/cleanup and
deadline stops, bounded fatal inspection, exact symptom attribution, root/child
distinction, and absent/truncated debugger coverage. Schema 2 regressions reject
missing register fields, invalid unsigned register values, unmatched calls,
wrong thread/context identities and malformed summary counts while preserving
valid false-return/zero-PC observations. Existing shared workload tests continue
to exercise the functional and accounting oracles.

With GDB, a compiler and a prepared SDK, the suite also executes a small native
and engine-only control. Its one known-address protection fault is handled by
making its data page writable; the application checks the delivered PC and
output. The debugger must report a true native translation return and the exact
assembly fault label as the post-translation PC. Set `DURANTE_TEST_CONTEXT_SDK`
to an explicit prepared SDK locally; a missing explicit SDK or required tool is
a test failure. CI installs GDB, verifies the required tools, and sets that SDK
variable for the unit suite so the live control is required. Outside CI, the
standard SDK path is used when no override is given; missing optional tools or
that default SDK produce an explicit skip, which does not validate the control.

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

The initial logging refinement redirected debugger chatter to a separate bounded
transcript and preserved terminal/coverage metadata even after event exhaustion.
Fourteen focused configuration and recorder regressions passed, but these changes
had not yet established a successful live JVM context capture. The original
failed attempt was not reclassified.

### Follow-up live logging check

A separate, unchanged-input check of that revision completed both observations
in 101.513 seconds. The native control passed; the JVM under GDB exited normally
without a fatal report. Context capture still failed: 566,031 bytes of debugger
signal-stop frames remained in stdout, above its 64-KiB inspection limit, while
the separate debugger transcript held only 18,780 bytes. All process cleanup
completed, CPU affinity was restored, and the fourteen runtime/tool inputs matched
the preceding observation.

The 256-event budget was retained. The 190,745-byte context file contained the
configuration, 255 signal stops, the truncation marker, and both terminal records
(`inferior_exit` and `capture_summary`). This verifies terminal metadata survives
event exhaustion in the actual JVM run. The summary identified both missing
HotSpot/poll-stub probes; no delivered HotSpot context or original polling-PC
argument was obtained.

A small native GDB regression reproduced the output-routing defect independently
of the JVM. On GDB 13.1, enabling logging from inside the Python script left
asynchronous `run`/`continue` stop frames on the original stdout. The harness now
applies the same logging commands through GDB's CLI before entering Python. The
regression uses eight handled signals and checks that the target's exact stdout
remains separate, all eight debugger stop frames and the exit message reach the
existing transcript, and signal/exit context records remain available. It failed
before this change and passed afterward. All fifteen focused regressions pass.
The 256-event, 1-MiB file, 120-second row, and 600-second campaign bounds and all
JVM/runtime options remain unchanged. The failed JVM attempt is preserved.

A single follow-up JVM check of the corrected tool completed in 60.526 seconds.
The native control and JVM under GDB both exited normally with exactly
`processes:204` plus its newline on stdout. Debugger output was preserved in a
609,537-byte transcript within the existing 1-MiB bound; the earlier stdout
inspection failure was absent. The context stream still reached its 256-event
budget and retained the truncation marker, inferior exit and capture summary.
The overall diagnostic correctly remained failed (exit 1), with both
HotSpot/poll-stub probes unobserved. Cleanup, input identity and CPU-affinity
restoration checks passed. This verifies the output correction on the JVM while
preserving the incomplete-context result; it does not establish successful
polling-context capture or explain the original safepoint abort.

BUG-006 remains open and its cause is unconfirmed; this logging correction is
not a JVM fix and changes no production runtime or client patch.

### Native translation observer follow-up

A bounded first-signal diagnostic found the JVM mapped in `/proc/<pid>/maps`
while GDB listed only the runtime core and its separate debug file. No libjvm
object-file event reached the previous installer. The two JVM symbols existed
in the selected JDK; their absence from capture was not evidence that the JVM
had never used them.

A separate tiny dynamic-library control established a second limitation. A
software breakpoint at the application function's original address hit correctly
in native execution. Under the engine it instead produced an unrecognized
SIGTRAP in the generated code cache, with no original-address breakpoint hit;
that diagnostic was intentionally stopped and preserved. Application-entry
software breakpoints were therefore removed from the JVM observer.

The first native-runtime probe attempted installation during the core's object
file callback and rejected an address outside the then-observed mapping. The
application itself still completed correctly. Deferring installation to a real
signal stop resolved the mismatch. At that stop, GDB already resolved the native
symbol to the correct mapped address, so an experimental explicit symbol reload
and ELF parsing fallback were removed. The final observer validates the existing
symbols and their GNU build IDs without changing global debugger configuration.
The known-fault native/engine control and all eighteen focused tests passed
with no skips and unchanged inputs. Its translated PC matched the independent
assembly fault label and the application validated the delivered PC itself.

The final one-native/one-debugger JVM run completed in 20.542 seconds (20.294
seconds inside the harness), with both targets returning the exact
`processes:204` output and exit 0. The observer saw 6,116 raw signal-delivery
stops, retained the declared first sixteen, and captured all three native
translation entry/return pairs. All three returned true with nonzero resulting
PCs. The stream used 26 events, with no truncation, outstanding return or missing
native probe, and the separate transcript was 607,876 bytes. Input identities,
CPU-affinity restoration and process cleanup passed. The diagnostic exited 0;
HotSpot handler/poll-stub coverage remained explicitly unavailable. This verifies
that the native translation observer works on the selected JVM, while leaving
the original intermittent safepoint failure and its cause unresolved.

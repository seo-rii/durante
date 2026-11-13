# Managed runtime workloads

`tests/runtime_workloads.py` exercises bounded Java, CPython and Node workloads
through the actual `durante run` CLI. This supplements the startup, arithmetic,
interpreter/JIT and library checks in [QUALIFICATION.md](QUALIFICATION.md).
Every selected runtime and every execution mode is required. There are no
known-failure exceptions or optional-runtime skips in this harness.

| Workload | Independent result and checks |
| --- | --- |
| Threads | Two joined workers calculate `sum(1..10000) * (1 + 2)`, producing `threads:150015000`. Worker exceptions and nonzero exits fail the parent. |
| Allocation and GC | Allocate 2,048 1-KiB buffers with complementary endpoint bytes, producing a checksum of 522,240. Explicit collection must be observed, and the final 128 retained buffers must preserve their endpoint data and checksum 32,640. |
| Child processes | Two sequential child runtimes receive seeds 7 and 11. Their individually checked outputs contribute `seed² + 17`, producing `processes:204`. Each child must exit normally. |

Java's GC fixture uses collection counts from the management API with Serial GC.
CPython requires a positive cyclic-collection count. Node observes actual `gc`
performance entries after explicit GC, using the documented
[performance observer API](https://nodejs.org/api/perf_hooks.html#garbage-collection-gc-details).
The number and timing of collections are deliberately absent from the output
oracle. The fixtures do not assert that a particular method was JIT compiled or
that deoptimization occurred.

## Execution controls and accounting

Each workload runs in four modes:

1. Native runtime, establishing the fixture's exact output and normal exit.
2. DynamoRIO without the counter, distinguishing engine compatibility failures.
3. The Durante client with REP telemetry disabled, collected by the Rust CLI.
4. `durante run`, with its normal REP-enabled client, lifecycle supervision,
   report publication and cleanup checks.

All four modes must produce the independent expected output and empty stderr.
The supervisor must also report a normal target exit, complete descendant and
record cleanup, and its unchanged diagnostic measurement/security contract.
The client and supervisor aggregates must equal the Python reference collector's
result from the retained raw records, including JSON field types. Validity,
process/image counts, parent links, each child's image count and REP mode are
checked explicitly. Thread fixtures require at least three accounted threads
in the root image. Runtime instruction counts need only be positive and
structurally consistent: scheduling and GC make exact instruction totals unstable.
Handled signals remain visible in measurement quality, and unsupported optional
REP forms remain `partial` telemetry.

The thread and allocation fixtures have one process and one image. CPython and
Node each launch two children, requiring three processes and five images
(the parent plus each child's initial fork image and target exec image).
Java's process fixture runs twice, explicitly selecting `FORK` and `POSIX_SPAWN`.
`FORK` requires the same 3/5 topology. `POSIX_SPAWN` requires three processes and
seven images because each child execs `jspawnhelper` before the Java target.
These launch mechanisms and helper are implemented in
[OpenJDK 17 ProcessImpl](https://github.com/openjdk/jdk17u/blob/master/src/java.base/unix/classes/java/lang/ProcessImpl.java)
and [OpenJDK 21 ProcessImpl](https://github.com/openjdk/jdk21u/blob/master/src/java.base/unix/classes/java/lang/ProcessImpl.java).
The helper images cannot silently disappear from accepted accounting.

## Run locally and in CI

Build the pinned SDK, counter and release CLI as described in the README. Use
Python 3.11 or newer for the harness, Linux x86-64, `prlimit`, Node and a complete
JDK. The default run requires Java 17, CPython and Node:

```sh
python3 tests/runtime_workloads.py \
  --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 --client build/libdurante.so \
  --runner-bin target/release/durante --output-dir .results/runtime-workloads \
  --java /path/to/jdk17/bin/java --javac /path/to/jdk17/bin/javac --java-major 17
```

For Java 21, select both executables from that JDK and pass `--java-major 21`.
The harness checks the actual runtime and compiler major versions before fixture
compilation. Use repeated `--runtime` arguments to request a local subset, for
example `--runtime cpython --runtime node`. A missing selected runtime fails;
it never becomes successful partial coverage. Use a fresh output directory for
every run. Each complete default matrix contains ten workloads and forty
observations, including both Java process-launch mechanisms.

The separate `runtime-workloads` workflow runs both Java 17 and Java 21 jobs on
Ubuntu 24.04. Each job also requires CPython 3.12 and the runner's Node installation.
It selects `JAVA_HOME_17_X64` or `JAVA_HOME_21_X64` explicitly, verifies the tools,
builds the pinned SDK/client/CLI and preserves all workload results even on failure.
Those JDK environment variables are documented by
[GitHub's Ubuntu 24.04 runner image](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md#java).
Point releases and host images can change; actual versions and input hashes are
recorded in every result. Existing PyPy coverage remains in `runtime_compat.py`;
this added workload suite does not claim PyPy GC or threading qualification.

```sh
python3 -m unittest discover -s tests -p 'test_runtime_workloads.py' -v
```

The unit suite executes the real CPython workloads and exercises missing runtimes,
wrong Java majors, omitted or duplicate modes, missing child images, altered
measurements, promoted trust claims, cleanup failure and changed input files.

## Bounds, provenance and interpretation

Java uses a 64-MiB heap, 256-KiB thread stacks, 32-MiB compressed-class/code-cache
reservations, two active processors and Serial GC. Node uses a 64-MiB old-space
limit and a 2-MiB semi-space limit. Its two workers additionally use 16-MiB code
ranges and 1-MiB stacks through the documented
[worker resource limits](https://nodejs.org/api/worker_threads.html#new-workerfilename-options),
so their JIT reservations fit the address-space ceiling. The harness applies the existing per-process
2-GiB address-space, 40-second CPU, 1-MiB regular-file and zero-core limits through
`prlimit`. The default wall deadline is forty seconds per observation, with five
extra seconds around the supervised mode for report/cleanup handling. Child
fixtures also impose fifteen-second deadlines. The twenty-minute campaign bound
is checked between observations; a final in-progress observation can exceed it
by its own bounded deadline. These are fixed fixture bounds, not runtime-wide
capacity claims or aggregate cgroup limits.

An outer subreaper owns orphaned test descendants and checks cleanup even after
failure. Inputs include the source fixtures and imported harness modules, selected
runtime executables, compiler, `jspawnhelper`, `libjvm`, generated Java class,
`drrun`, release core, counter, collector, harness Python and `prlimit`. Bounded
before/after identity checks invalidate the whole result if an input changes or
cannot be inspected. Results retain each mode's command, stdout/stderr, raw records,
supervisor report, observed runtime versions and provenance errors.

Exit 0 requires the complete requested matrix, unchanged inputs and successful
cleanup. Exit 1 means a workload, accounting, provenance, setup or cleanup failure;
inspect `results.json`. Invalid command-line syntax or a nonfresh/unwritable output
directory exits 2. Report publication failure also exits 1.

These results cover the fixed workloads and runtime versions that actually ran.
They do not qualify arbitrary libraries, concurrent code patching, all GC/JIT
configurations, virtual threads, dynamic agents, or the separate `isolate` profile.
Before/after hashes do not attest a hostile host or inventory every dynamic
dependency. All counters and reports remain application-mutable,
`diagnostic_only`, and unsuitable for trusted CPU-time, billing or verdicts.

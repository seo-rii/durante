# Managed workloads inside the isolation profile

`tests/isolated_runtimes.py` requires actual `durante isolate` execution of the
fixed Java, Node and CPython thread, allocation/GC and child-process workloads.
The same fixture outputs are checked natively and inside isolation. Java child
creation exercises both `POSIX_SPAWN` and `FORK`. These are small, explicit
profiles, not a claim that arbitrary Java/Node/Python programs fit the sandbox.

The dedicated image uses Debian trixie OpenJDK **21**, CPython and the official
self-contained Node **22.23.5** Linux x64 build, pinned by archive SHA-256. Its exact
Docker image ID and runtime version strings are recorded. A different JDK major,
missing runtime, different Node version, failed native control or missing matrix
row fails qualification.
Runtime package updates can change the image: the recorded image ID identifies
the tested build, while the base image digest pins its starting filesystem.

The production namespace policy and fixed resource ceilings are unchanged.
Each target has a 1 GiB address-space ceiling; Java uses a 64 MiB heap, 256 KiB
thread stacks, 32 MiB compressed-class/code spaces and Serial GC. Node uses a
64 MiB old-generation heap, 2 MiB semi-spaces and bounded worker resources.
The native controls use the same address-space ceiling. The private outer
container has 1 GiB memory, 128 tasks, two CPUs, no network and a read-only root.
Each fixture gets a 120-second execution deadline and a 64 KiB capture quota;
the external owner imposes an independent ten-minute deadline on each profile.

The fixed guest environment sets `MALLOC_ARENA_MAX=2`, and native controls use
the same setting. This bounds glibc's allocator arenas: their address-space
reservations can otherwise exhaust the 1 GiB ceiling while resident memory is
still small. The two JavaScript workers, Node heap/code-range limits and JIT
remain enabled. Allocator sharing can increase contention in allocation-heavy
threaded programs; this setting is part of the explicit isolation profile.

Debian's JDK normally links configuration files into `/etc`. The image copies
its own default JDK configuration into its JDK tree under `/usr/lib/jvm`, which
the existing isolation profile already exposes. It does not bind extra host
configuration into the guest or change host security policy. Other runtime
layouts must be qualified separately.
The official Node build keeps its builtins within `/usr/lib/durante-node`.
Debian's Node package instead requires externalized builtins under
`/usr/share/nodejs`, which this isolation profile does not expose.

## Both installation paths are executed

The first container runs an ordinary prepared CLI/SDK/client/launcher
installation. The second runs a freshly packaged, statically verified bundle
after renaming its version directory to a path containing a space. Only the
relocated bundle is mounted in that container; the original SDK, client, CLI
and Bubblewrap installation are absent. The source mount contains only the
required harness/fixture files, so it cannot expose CI build directories.

Both paths require `doctor --profile isolate` (the wrapper's `doctor-isolate`),
all native/isolate workload pairs, exact captured stdout and empty stderr,
complete outer and inner executions, exact process/image topology, worker
thread observations, positive diagnostic instruction totals, and process and
scratch cleanup. Java's GC fixture and Node's GC observer must actually observe
collection; CPython requires a cyclic collection. Process/image totals must
agree, but raw records are intentionally not exported from the inner sandbox.
The existing non-isolated workload suite supplies independent collector checks.

Java's subprocess input streams query queued bytes with FIONREAD; denying it
can cause `IOException: Illegal seek`. Node/libuv uses FIONBIO to make its
stdio pipes nonblocking; denying it can cause `Error: open EPERM`. The filter
permits these exact requests alongside FIOCLEX/FIONCLEX descriptor flags.
FIONBIO changes O_NONBLOCK on an existing open file description, shared by
dup/fork and also controllable through fcntl; it is not read-only or a general
driver ioctl allowance. A native probe checks pipe/socket queued bytes,
nonblocking set/clear with other flags and queued data preserved, and descriptor
flags. It still requires EPERM for TCGETS, TIOCSTI, FICLONE and an unknown request.

A native probe also runs through the relocated wrapper's `isolate` path. Its
captured `/proc/self/maps` must show Durante, drmgr and DynamoRIO loaded from the
relocated version. This tests optional launcher packaging and real captured
execution, in addition to the non-isolated [bundle smoke](BUNDLE.md).

The host owner remains outside the job cgroup. The trusted container driver
stays alive after publishing its observations, so only external whole-job
termination can complete qualification. The owner removes its uniquely named
container and independently verifies an empty or absent host cgroup. An
unavailable Docker/cgroup environment or driver timeout fails the suite.

Each workload's `result.json` records cgroup resource snapshots before and after
the command in `resources.before` and `resources.after`. `resources.delta` contains
changes in `memory.events`, `pids.events` and `cpu.stat`. The external owner also
captures these values after container startup and before container teardown. A driver that
exits without publishing its report retains Docker's `ExitCode` and `OOMKilled`
fields when observed. Missing required counters fail the observation; kernels
without `memory.peak` explicitly report `memory_peak_bytes: null`.
Aggregate rows link to the complete per-observation file through
`resource_evidence`, relative to the profile directory, instead of duplicating
these snapshots in `worker.json`. The aggregate retains its 64-KiB read bound.

These read-only snapshots cover the whole disposable container, including the
driver. Memory peaks are cumulative since container creation, not per-workload
peaks. CPU time and throttling include driver overhead. An `oom_kill` or PID-limit
event is useful evidence alongside a child's exit/output; its absence does not
exclude address-space allocation failure under `RLIMIT_AS` or another process
exit cause. See the kernel's [cgroup v2 controller documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html).

## Repeated Java child observations

The separate `--suite java-children` campaign uses only the relocated bundle and
performs three rounds of `POSIX_SPAWN` followed by `FORK`, each with a required
native control and isolated execution: **12 observations**. Every observation
gets a fresh result directory and the unchanged Java fixture, heap/JIT options,
exact output and process/image checks. The child deadline remains 45 seconds,
each observation retains its 120-second bound, and the external owner keeps its
ten-minute profile deadline. There are no success retries or optional repeats.

The worker writes `plan.json` before workload observations and reports any
`unrun_observations` in `worker.json`. A failure, omitted repeat, unavailable
resource evidence or incomplete cleanup fails the campaign. Original reports
are retained; a passing campaign is a non-reproduction, not proof that the
intermittent Java child failure below is fixed.

Append `--suite java-children` to the command below, using fresh output and work
directories, to run this campaign locally. Omitting `--suite` still selects the
full 40-observation matrix across both installation profiles. CI requires both
campaigns and uploads `.results/isolated-runtimes/` and
`.results/java-child-runtimes/` together.

## Reproduction and evidence

Build the pinned SDK/client/CLI and launcher using the existing
[isolation workflow](../.github/workflows/isolation.yml), then:

```sh
docker build -f docker/isolated-runtimes.Dockerfile -t durante-isolated-runtimes .
python3 tests/isolated_runtimes.py --image durante-isolated-runtimes --java-major 21 \
  --cli /trusted/cli/durante --sdk /trusted/sdk --client /trusted/client/libdurante.so \
  --launcher /trusted/isolation-tool/bwrap --output-dir /private/new-results \
  --work-dir /private/new-transient-work
```

Use a nonroot operator with a local Docker daemon and readable host cgroup v2
state. Installation paths and their parents must be trusted; outputs must be
fresh and separate. Archive, unpacked bundles and staged driver payloads go in
the work directory; CI uploads the evidence directory only.
The [dedicated workflow](../.github/workflows/isolated-runtimes.yml) builds
all components and requires this suite on pull requests and main pushes.

The output contains the archive digest/revision and bundle manifest, exact native
outputs, isolated reports and captured files, runtime versions, per-case failure
reasons, source/artifact hashes before and after the run, and independent owner
cleanup observations. Both original inputs and the actual staged driver,
relocated CLI/launcher/manifests/runtime libraries must remain unchanged.
For Java child failures, inspect the case's `captured/stderr`: the fixture records
the child seed, exit code and up to 128 stdout bytes as Base64, as described in
the [shared workload diagnostics](RUNTIME_WORKLOADS.md#execution-controls-and-accounting).
`results.json` must have both `passed` and
`cleanup_complete` true. Missing reports and partial matrices never count as
successful coverage.

Passing CI does not qualify every local build. Additional Java 21 child-process
observations with a locally built runtime have intermittently failed with an
engine signal-handler crash or a child output/exit mismatch; their cause remains
unresolved. Retain those failed reports when comparing another build or run.
The child diagnostics above help distinguish the outcomes; a later passing run
alone does not establish a fix.

All reports retain `authority: diagnostic_only` and `integrity_verified: false`.
Containment and complete arithmetic results do not make application-mutable
instruction totals trusted CPU time, TLE or billing evidence. Follow the
[execution-owner contract](EXECUTION_OWNER.md) and [operational recovery guide](OPERATIONS.md).

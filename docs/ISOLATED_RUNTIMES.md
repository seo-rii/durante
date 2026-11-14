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
Each isolated target gets a 120-second execution deadline. The capture harness
permits 125 seconds for both native and isolated commands, with a 64 KiB capture
quota. The external owner imposes an independent ten-minute deadline on each profile.

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
Snapshots also record the kernel's `cpuset.cpus.effective` as
`cpuset_cpus_effective`, or `null` when the file is unavailable. Each owner row
records `requested_cpuset_cpus`, which is `null` when CPU placement was omitted.
An explicit placement request requires effective CPU-set evidence; its absence
fails the campaign.
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
isolated targets retain their 120-second bound, the capture harness retains its
125-second bound, and the external owner keeps its ten-minute profile deadline.
There are no success retries or optional repeats.

The worker writes `plan.json` before workload observations and reports any
`unrun_observations` in `worker.json`. A failure, omitted repeat, unavailable
resource evidence or incomplete cleanup fails the campaign. Original reports
are retained; a passing campaign is a non-reproduction, not proof that the
previously observed Java child failures are fixed.

Append `--suite java-children` to the command below, using fresh output and work
directories, to run this campaign locally. Omitting `--suite` still selects the
full 40-observation matrix across both installation profiles. CI requires both
campaigns and uploads `.results/isolated-runtimes/` and
`.results/java-child-runtimes/` together.

For a fixed CPU-placement comparison, also append `--cpuset-cpus 0,1` to the
command below. CPU lists and ranges such as `0-3,6` are accepted. Choose CPUs
available to the Docker host and check their topology when selecting distinct
physical cores; logical CPU numbers are machine-specific. The option constrains
the disposable container's CPU placement while retaining its two-CPU quota and
all workload limits. Omitting it preserves default placement. CI uses default
placement for the full matrix and `--cpuset-cpus 0,1` for the repeated Java-child
campaign. Those logical IDs need not represent distinct physical cores on every
runner.

A historical comparison, before the upstream correction below, ran the two
conditions sequentially on one host with the same SDK, CLI, client, image and
Java fixture, for **24 planned observations**:

| CPU placement | Native observations passed | Isolated observations passed |
| --- | --- | --- |
| Default allowed CPUs | 6/6 | 6/6 |
| Two distinct physical cores (`0,1` on that host) | 6/6 | 1/6 |

All five failed isolates reported `child output or exit mismatch` with
`seed=7 exit=139` and empty child stdout, followed by rejection because
`process has no completed final image`. Both launch mechanisms were affected.
The failures completed in 33.304–35.750 seconds, with no `child deadline` marker
or memory/PID limit events. Both campaigns completed all planned observations;
cleanup and input-hash checks passed. Exit 139 is the recorded Java child exit
value; the retained evidence does not directly identify its terminating signal.
The sequential condition order supports a CPU-placement correlation on this
build, without establishing the cause. CPU placement alone does not fix the
failure.

## Upstream runtime correction

The prepared runtime now includes the exact upstream correction from
[DynamoRIO #7749](https://github.com/DynamoRIO/dynamorio/pull/7749)
([commit 985af57](https://github.com/DynamoRIO/dynamorio/commit/985af57d7c3d73e7a1100d98e412584968588258)).
It prevents internal suspend-signal reentry between resume notification and
handler return. Upstream originally reproduced the issue with the AArch64
`api.detach_state` test; Durante's Java/x86-64 observations below are separate.
The source pin remains `6847bb210bea0ca11d1bf47704383d6a075da490`; the updated
lifetime-patch digest is
`b44627a42f4f0174e843158e3b3473cbcae26e489da55582008f043d0665716c`.
Rebuild the SDK, client and CLI together; isolate rejects the previous SDK digest.

Two fixed comparisons used OpenJDK 21.0.13+9 and the same image, fixture bytecode,
client, launcher, JVM flags and limits on two distinct physical cores (`0,1` on
that host). Each observation had a fresh owned container and identical guest
paths. `POSIX_SPAWN` and `FORK` were both exercised, with the old/new runtime order
alternated between rounds. There were no retries, forced scheduling or runtime
instrumentation changes.

| Comparison | Native passed | Previous runtime passed | Updated runtime passed |
| --- | --- | --- | --- |
| Initial 18 observations | 6/6 | 1/6 | 6/6 |
| Separate 24-observation confirmation | 8/8 | 0/8 | 8/8 |

The initial comparison **failed its predeclared symptom gate**: four baseline
failures matched the child-exit family, but one baseline run instead reached a
HotSpot `sharedRuntime.cpp:560` safepoint-PC guarantee and root SIGABRT 6. That
result remains failed. The separate confirmation declared this secondary JVM
abort in advance and excluded it from evidence of correcting the original
failure; it did not occur in the confirmation.

The confirmation's baseline failures were six first-child exit/output mismatches
and two root SIGSEGV 11 exits without stderr. All eight updated-runtime runs
passed the exact output, process/image accounting and cleanup checks. Every
planned observation completed, source/artifact hashes stayed unchanged, and
resource evidence and external teardown checks passed. The raw campaign retained
exit 1 for its baseline failures; the separately evaluated before/after comparison
passed. This supports the correction for this tested profile without assigning
every historical child exit or the distinct JVM abort to the same cause.

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

Passing CI does not qualify every local build. Retain the earlier failed reports
alongside the runtime comparison above. These observations cover the tested
fixture and environment; they do not qualify arbitrary Java workloads, JVM
configurations or hosts, or establish that every earlier failure shared a cause.

All reports retain `authority: diagnostic_only` and `integrity_verified: false`.
Containment and complete arithmetic results do not make application-mutable
instruction totals trusted CPU time, TLE or billing evidence. Follow the
[execution-owner contract](EXECUTION_OWNER.md) and [operational recovery guide](OPERATIONS.md).

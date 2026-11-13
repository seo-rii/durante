# Opt-in Linux isolation

`durante isolate` runs the diagnostic counter behind an outer watchdog and a
source-pinned, locally patched Bubblewrap PID 1. It is an experimental Linux
x86-64 namespace profile. The application still shares an address space with its
counter: **instruction totals remain application-mutable**. Isolation does not
make those totals authoritative for judging or CPU/TLE enforcement.

## Operator prerequisites

An external execution owner must first supply a private unified cgroup v2
namespace, with finite `memory.max` (at most 4 GiB), `pids.max` (at most 256), and
`cpu.max` (at most eight cores). The CLI checks these kernel files and refuses
unsupported configurations. It does not create, migrate or modify host cgroups.
The external owner must enforce aggregate lifetime, disk/I/O limits and teardown
if the outer controller is killed or OOM-killed. It must also enforce output byte
quotas unless bounded capture is enabled as described below. Namespaces
share the host kernel; use an independently maintained VM boundary when needed.

See the [external execution-owner guide](EXECUTION_OWNER.md) for the bounded
controller-failure tests, required teardown evidence and scenarios that remain
unqualified. The guide is also included in versioned runtime bundles.

Unprivileged user namespaces and nested PID, network, mount, IPC and UTS
namespaces must be available. Distribution policies may deny them. The profile
fails closed rather than changing host sysctls, AppArmor or security policy.
The controlled Docker regression job explicitly relaxes its disposable outer
container's masking/seccomp/AppArmor to permit the inner namespace setup; those
flags are test configuration, not instructions to weaken a shared host. The
job uses Ubuntu 22.04 without changing host policy: Ubuntu 24.04's default
[unprivileged-userns AppArmor restrictions](https://documentation.ubuntu.com/release-notes/24.04/)
deny capabilities in unconfined user namespaces even when creation succeeds.
An unsupported deployment must be explicitly configured by its operator; the
CLI never installs an exception itself.

Use operator-owned, immutable CLI, client, patched SDK and launcher installations.
The manifests and binary hashes detect inconsistent local installations; they
are not signatures against a malicious installer. The SDK path must identify the
bounded SDK installation, not a general directory of host files.

Build prerequisites include Python 3.11 or newer, Meson, Ninja, pkg-config,
libcap development files, patch and a C compiler. Build a fresh pinned launcher
without installing a setuid executable:

```sh
python3 toolkit/build_isolation.py --output "$PWD/.local/bwrap" --work-dir "$PWD/.local/build-bwrap"
```

The builder writes both `.local/bwrap` and
`.local/bwrap.durante-isolation.json`. Install the matching pair together in the
operator-owned location: `/trusted/bwrap` in the examples requires the adjacent
`/trusted/bwrap.durante-isolation.json`. If the executable is renamed, the
manifest name must still be the executable's name plus `.durante-isolation.json`.
Keep both files protected from group/other writes and preserve the launcher's
executable, non-setuid permissions. A missing or mismatched manifest is rejected
before launch.

Inside the preconfigured bounded execution environment, invoke:

```sh
# All three standard descriptors must be pipes or supported inert devices.
printf '' | durante isolate --launcher /trusted/bwrap \
  --drrun /trusted/sdk/bin64/drrun --client /trusted/libdurante.so \
  --workspace ./input --report ./new-report.json --timeout-ms 5000 \
  -- /usr/bin/python3 /work/main.py 2>/dev/null | cat
```

A terminal descriptor is rejected. Production callers should use a process API with piped stdio, a
bounded output reader, and an externally protected report directory. Regular
files, sockets, named FIFOs, terminals and control handles are rejected as
inherited stdio: `/proc/self/fd` can expose capabilities beyond ordinary reads or
writes. Anonymous pipes and the exact null/zero/random/urandom devices are
accepted. Extra inherited descriptors are closed on exec.

## Bounded output capture

Use `--output-dir NEW_DIR` to keep target stdout/stderr behind anonymous pipes and
collect a bounded prefix into new private host files. The directory must not
exist; the CLI creates mode 0700 with mode-0600 `stdout` and `stderr` files. The
directory is not added to the guest's mounts, and no output-file descriptor is
passed to the guest. Keep it outside the SDK and exposed system directory trees
so their existing read-only mounts cannot expose the empty capture files.
`--output-limit-bytes`
requires `--output-dir` and caps the **combined** bytes from both streams, with a
default of 1 MiB and a range of 1 byte through 64 MiB:

```sh
durante isolate --launcher /trusted/bwrap \
  --drrun /trusted/sdk/bin64/drrun --client /trusted/libdurante.so \
  --workspace ./input --report ./new-report.json --timeout-ms 5000 \
  --output-dir ./new-output --output-limit-bytes 1048576 \
  -- /usr/bin/python3 /work/main.py </dev/null
```

Captured stdout/stderr are not forwarded to the caller's standard descriptors;
those descriptors may consequently be terminals or files in this mode. Stdin
still follows the safe-handle restrictions above. The caller must choose an
externally protected report/output parent directory and a responsive filesystem.

The owner alternates nonblocking reads from both streams with a fixed budget per
watchdog tick. At most the quota is retained, with two pre-reserved buffers each
bounded by that quota. Data is persisted only after owned children have been
reaped, so destination-file writes cannot block the active guest's watchdog.
The CLI writes at most the quota in total across the two files. Their split depends on pipe
scheduling; the files do not establish a combined ordering between stdout and
stderr. Exceeding the quota yields `execution.state: "output_limit"`, CLI exit
125, bounded tree cleanup, and no accepted `untrusted_inner_report`. Exactly the
quota followed by EOF is accepted. Pipe/publication failure yields
`output_failed`; cleanup failure remains `cleanup_failed`.

The outer `output` object reports the configured `limit_bytes`, both
`*_captured_bytes`, `observed_bytes`, `truncated`, `published`, and `complete`.
`observed_bytes` counts only bytes actually read, including the first over-limit
read; it is not a claim about all bytes the target attempted to emit. A forced
stop may leave queued bytes unread. `published` means both captured files passed
inode, link-count, permissions and length checks before and after writing.
`complete` additionally requires EOF on both streams without truncation; it
says nothing about measurement integrity or target success. Failed publication
can leave partial files with `published: false`; no unsafe path is deleted or
overwritten during recovery. Without `--output-dir`, `output.mode` is `inherited`
and `external_quota_required` remains true.

This closes the output-memory/storage-byte gap for a live controller. It does
not create a guardian outside the execution cgroup, prevent that controller from
being OOM-killed, impose aggregate disk/I/O bandwidth limits, or guarantee a
report after SIGKILL. The external service owner must still provide those
resource and death/teardown policies. Instruction counters remain diagnostic.

## Boundary and results

The input becomes a read-only snapshot at `/work`, limited to 256 entries, 64 MiB
and depth eight. Symlinks, hardlinks, devices, sockets and FIFOs are rejected
before content opens. Existing `/usr/bin`, `/usr/lib` and `/usr/lib64` trees are
mounted read-only in their entirety, along with the trusted SDK and runtime
components; this is not a per-file system-library allowlist. Keep secrets out of
those exposed trees and use a minimal outer execution image. Environment
variables are cleared; a private 128 MiB `/tmp`, a fresh `/proc`, minimal devices,
no network, dropped capabilities and no-new-privileges apply.

The launcher and its descendants inherit these equal soft/hard per-process
limits, which include the instrumentation runtime's resource use:

| Limit | Value |
| --- | --- |
| Virtual address space (`RLIMIT_AS`) | 1 GiB |
| CPU time (`RLIMIT_CPU`) | `ceil(timeout_ms / 1000) + 1` seconds |
| Regular-file size (`RLIMIT_FSIZE`) | 16 MiB per file |
| Open descriptors (`RLIMIT_NOFILE`) | 1024 |
| Core dump size (`RLIMIT_CORE`) | 0 |

These fixed ceilings restrict runtime compatibility and complement the required
aggregate cgroup limits. A per-file size ceiling does not bound total storage or
the combined output stream; use bounded capture for the latter.

An AMD64 seccomp policy denies alternate syscall ABIs, tracing, host namespace
access, mount operations, network connections, keyrings, BPF, asynchronous I/O,
extended attributes and mutating ioctls. Private AF_UNIX stream/seqpacket pairs
remain available for process-launch handshakes; datagram pairs and socket
connect/bind/listen/accept are denied. The ioctl allowlist is limited to
FIOCLEX/FIONCLEX descriptor flags, the read-only FIONREAD queued-byte query,
and FIONBIO's O_NONBLOCK flag on the existing open file description. That
nonblocking flag is shared by dup/fork and is also controllable through fcntl;
it is not a read-only operation. Terminal operations, filesystem-mutating
commands and every other ioctl remain denied.
This deliberately restricts application compatibility.

The guest can write one precreated capture file, not the outer report or its
parent directory. Results retain `integrity_verified: false` and place any
accepted diagnostic report under `untrusted_inner_report`. Timeout, killed inner
supervision or cleanup failure produces no accepted inner report. Error text
copied from the guest is explicitly untrusted. The patched namespace PID 1 is
non-dumpable before the guest forks, blocking guest access to its memory and
inherited descriptors. A private pidfd guard arms parent-death handling before
the initial namespace setup wait and verifies that the outside monitor is alive;
it is rearmed and closed before the guest forks. Startup monitor-death regressions
exercise that window without exposing the host pidfd to the guest. A separate
external owner remains required for forced
controller death and kernel resource failures.

`tests/isolation_smoke.py` exercises native and Python subprocess execution,
PID/network separation, supervisor memory/descriptor denial, read-only mounts,
missing host secrets, seccomp restrictions, inner-supervisor SIGKILL, an outer
watchdog deadline, unsafe input nodes and inherited regular-file rejection.
Capture regressions also exercise binary output at the exact quota, concurrent
stdout/stderr flooding, descendants retaining or closing pipes, repeated
cancellation, and killed inner supervision without accepted counts.
These bounded regressions establish the tested cases, not absence of escapes.

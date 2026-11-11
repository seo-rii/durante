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
The external owner must enforce aggregate lifetime, output byte quotas, disk/I/O
limits and teardown if the outer controller is killed or OOM-killed. Namespaces
share the host kernel; use an independently maintained VM boundary when needed.

Unprivileged user namespaces and nested PID, network, mount, IPC and UTS
namespaces must be available. Distribution policies may deny them. The profile
fails closed rather than changing host sysctls, AppArmor or security policy.
The controlled Docker regression job explicitly relaxes its disposable outer
container's masking/seccomp/AppArmor to permit the inner namespace setup; those
flags are test configuration, not instructions to weaken a shared host.

Use operator-owned, immutable CLI, client, patched SDK and launcher installations.
The manifests and binary hashes detect inconsistent local installations; they
are not signatures against a malicious installer. The SDK path must identify the
bounded SDK installation, not a general directory of host files.

Build prerequisites include Meson, Ninja, libcap development files, patch and a C
compiler. Build a fresh pinned launcher without installing a setuid executable:

```sh
python3 toolkit/build_isolation.py --output "$PWD/.local/bwrap" --work-dir "$PWD/.local/build-bwrap"
```

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

## Boundary and results

The input becomes a read-only snapshot at `/work`, limited to 256 entries, 64 MiB
and depth eight. Symlinks, hardlinks, devices, sockets and FIFOs are rejected
before content opens. Only selected system binaries/libraries and trusted
runtime components are mounted read-only. Environment variables are cleared;
private temporary storage, a fresh `/proc`, minimal devices, no network, dropped
capabilities and no-new-privileges apply. Per-process CPU, address-space, file-size
and descriptor limits complement the required aggregate cgroup limits.

An AMD64 seccomp policy denies alternate syscall ABIs, tracing, host namespace
access, mount operations, network connections, keyrings, BPF, asynchronous I/O,
extended attributes and mutating ioctls. Private AF_UNIX stream/seqpacket pairs
remain available for process-launch handshakes; datagram pairs and socket
connect/bind/listen/accept are denied. Only the process-local FIOCLEX/FIONCLEX
ioctls are allowed. This deliberately restricts application compatibility.

The guest can write one precreated capture file, not the outer report or its
parent directory. Results retain `integrity_verified: false` and place any
accepted diagnostic report under `untrusted_inner_report`. Timeout, killed inner
supervision or cleanup failure produces no accepted inner report. Error text
copied from the guest is explicitly untrusted. The patched namespace PID 1 is
non-dumpable before the guest forks, blocking guest access to its memory and
inherited descriptors. A separate external owner remains required for forced
controller death and kernel resource failures.

`tests/isolation_smoke.py` exercises native and Python subprocess execution,
PID/network separation, supervisor memory/descriptor denial, read-only mounts,
missing host secrets, seccomp restrictions, inner-supervisor SIGKILL, an outer
watchdog deadline, unsafe input nodes and inherited regular-file rejection.
These bounded regressions establish the tested cases, not absence of escapes.

# External instruction-counter feasibility experiment

`experiments/external_perf.py` asks a narrow question: can a kernel-owned,
parent-created hardware instruction counter preserve a known increment of work
when the fixture tries to disable counters or crosses fork, thread and exec
boundaries? It executes only bundled finite native fixtures. It neither adds a
Durante production backend nor accepts an arbitrary target command.

```sh
python3 -m unittest discover -s tests -p 'test_external_perf.py' -v
python3 experiments/external_perf.py \
  --output-dir .results/external-counter --repeats 3
```

The output directory must be new. Linux x86-64, Python 3, a C compiler with
pthread support and a permitted hardware PMU are needed. The script compiles the
helper with warnings as errors and records source/binary SHA-256 before and after
the run, kernel, CPU model/microcode, event configuration and the existing
`perf_event_paranoid` value. It changes no host permission, capability or sysctl.

Exit statuses are deliberately distinct:

| Exit | `qualification` | Meaning |
| --- | --- | --- |
| 0 | `supported_for_fixtures` | All native controls and hardware observations passed the finite matrix on this host. |
| 77 | `unavailable` | The host does not permit/provide the required event. No hardware qualification occurred. |
| 1 | `failed` | Compilation, lifecycle, coverage, work increment or another check failed. |

Every result retains `authority: diagnostic_only`, `integrity_verified: false`
and `production_backend_qualified: false`. An unavailable experiment is not a
passing measurement, and successful native or synthetic tests do not qualify a
hardware counter. CI preserves that distinction in the result artifact even
when an unavailable host is accepted for running the experiment workflow.

## Counter ownership and collection

The controller forks a child blocked before exec, then opens
`PERF_TYPE_HARDWARE / PERF_COUNT_HW_INSTRUCTIONS` attached to that child. The
descriptor is created after fork and with `PERF_FLAG_FD_CLOEXEC`. It never enters
the fixture's descriptor table. The controller becomes nondumpable before fork;
the child restores its own dumpability and acknowledges readiness before attach,
so the controller's protection cannot accidentally deny same-UID perf access.
The child also checks that descriptors 3–1023 are closed after exec.

The event is initially disabled, enabled by exec, inherited across children,
pinned, and excludes kernel/hypervisor execution. Reading happens after normal
root exit and complete owned-child reaping. A short/failed read, zero count,
zero enabled time, or unequal enabled/running times rejects the observation;
the experiment never scales multiplexed counts. These controls use the Linux
[perf event ABI](https://man7.org/linux/man-pages/man2/perf_event_open.2.html).

Linux 6.1 `perf_event_task_disable()` iterates the calling task's owned event
list. This supports the hypothesis that a target's `PR_TASK_PERF_EVENTS_DISABLE`
does not disable the controller-owned event. The separate prctl manual has
conflicting wording, so the experiment tests the behavior rather than treating
that hypothesis as qualification. See the
[Linux 6.1 implementation](https://github.com/torvalds/linux/blob/v6.1/kernel/events/core.c).

PMU access depends on kernel policy, capabilities, hardware and virtualization.
The probe reports the actual syscall error instead of inferring a hardware
failure from policy alone. Capability or host policy provisioning belongs to
the operator, outside this experiment. See the kernel's
[perf security documentation](https://docs.kernel.org/admin-guide/perf-security.html).

## Executed matrix

Each mode has native controls at both fixed work sizes; metered observations
repeat 1–5 times (default 3). A fixed integer loop executes four ordinary
instructions per iteration. Increasing iterations from 200,000 to 2,000,000
must increase the recorded count by 7,200,000, within a 1% feasibility tolerance
for surrounding runtime work. Every mode and repeat must independently pass.
This is a work-increment oracle, not an exact whole-program instruction oracle.

| Mode | Work/attempt |
| --- | --- |
| `control` | Ordinary root-thread integer work. |
| `disable` | Call `PR_TASK_PERF_EVENTS_DISABLE` before the same work. |
| `descriptor` | Attempt disabling ioctls on fds 3–1023 and opening the controller's `/proc/<pid>/mem`; all must fail, then do the work. |
| `fork` | Do the work in one child and wait for its successful exit. |
| `thread` | Do the work in one new pthread and join it. |
| `exec` | Replace the process image before doing the work. |

The helper has a three-second wall deadline, two-second cleanup deadline,
subreaper ownership, parent-death checks and process-group cleanup. Fixtures are
limited to 256 MiB address space, two CPU seconds, no core/file writes and 1024
descriptors. The Python harness adopts/reaps known fixture descendants if the
helper exits unexpectedly. These controls are for this fixed, non-daemonizing
fixture family; they are not a sandbox for arbitrary hostile programs.

## Recorded local result and remaining work

On 2026-11-13, Linux `6.1.0-54-cloud-amd64`, AMD EPYC 7B12 and
`perf_event_paranoid=3`, all 12 native controls completed. The first real
`perf_event_open` returned `EACCES` (13), the pre-exec child was terminated and reaped,
and the experiment returned 77 / `unavailable`. Therefore the real counter's
disable resistance, inheritance and work increments remain unqualified here.

Even a future successful matrix will not establish trusted judging. Required
follow-up includes a separately isolated execution owner, hostile-target
signals and supervisor-death handling, all process/ABI transitions, PMU
availability and scheduling failures under load, architecture/firmware errata,
overflow behavior, and platform qualification. Counter ownership does not stop
a same-UID target from signalling an unisolated controller. Kernel-retired
instructions also differ from Durante's basic-block-entry metric and still do
not express CPU milliseconds or fair costs for SIMD, REP, JIT, cache misses and
system calls. There is no fallback from an unavailable/failed hardware counter
to an authoritative application-mutable count.

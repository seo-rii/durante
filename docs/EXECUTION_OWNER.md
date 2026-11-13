# External execution ownership

`durante isolate` requires an execution owner outside the job's cgroup. That
owner supplies finite memory, task, CPU and storage limits, imposes its own
deadline, and tears down the entire job after cancellation or controller death.
The owner must remain able to act when Durante is killed, stopped, out of PIDs,
or unable to write its final report. Durante's live-controller cleanup and
parent-death signals do not replace that owner.

Give each job a separate disposable cgroup and private storage. Keep the owner,
its control channel, deadline and result destination outside the job's resource
budget. On a controller failure, reject all counts and incomplete output, stop
the whole job through the resource owner, and independently verify that its
cgroup is empty or gone before reclaiming storage. Container removal alone is
not that verification. Never recursively clean paths taken from an untrusted
report, reuse another job's container/cgroup identity, or infer successful
execution from a missing report.

The test harness in `tests/execution_owner.py` models this contract with a local
Docker daemon. It is a fixed regression test, not a production job service. The
host Python process owns each container and verifies that its own cgroup is
outside the job's cgroup. A trusted PID 1 fixture driver launches the actual
outer `durante isolate` process. It intentionally stays alive after injecting
the fault, so killing Durante alone cannot make the teardown check pass.

| Case | Required observation |
| --- | --- |
| Successful control | A real isolated native fixture completes, publishes a valid diagnostic inner report, and passes process/scratch cleanup. |
| Controller SIGKILL | The native fixture and its detached child are running before SIGKILL reaches the outer controller; its status is `-SIGKILL`, without accepted counts. |
| Controller SIGSTOP | The running outer controller enters the stopped state. The separate owner terminates the job without relying on Durante's timeout. |
| Task exhaustion | At most 48 native fork attempts hit the container's 32-task limit; both `EAGAIN` and an incremented `pids.events:max` are observed. The owner rejects the job and tears it down. |
| Scratch storage exhaustion | A dedicated 8 MiB tmpfs returns `ENOSPC` within 16 bounded writes. The real controller fails before launching the target, without accepted counts. |

Every case requires a live, populated cgroup just before the owner's successful
kill. The owner removes the container and independently reads the host cgroup's
`populated` value and task list (or verifies that the cgroup disappeared) before
recording successful cleanup. A container that already stopped through its own
failsafe does not qualify as owner recovery. PID exhaustion does not need `docker exec`
to recover: a trusted driver already exists, and teardown runs outside the job.
The native children have a 30-second failsafe, the driver is finite, each Docker
command has a timeout, and the host owner has an observation deadline.

The job is limited to 1 GiB memory, one CPU, 32 tasks, a 64 MiB scratch tmpfs and
a separate 8 MiB failure tmpfs. Its root is read-only; networking is disabled;
installation/source mounts are read-only. Only that case's evidence directory
is writable on the host. The Docker-specific namespace/security settings match
the existing disposable [isolation CI profile](../.github/workflows/isolation.yml).
The harness creates only its own disposable container cgroups; it changes no
existing cgroups, host sysctls, security profiles or credentials.

After building the pinned installation, native fixture and existing disposable
runtime image as in isolation CI, run as the nonroot operator:

```sh
mkdir -p .results/owner-bin
cp target/release/durante .results/owner-bin/durante
cc -O2 -Wall -Wextra -Werror tests/execution_owner_probe.c \
  -o .results/owner-bin/owner-probe
python3 tests/execution_owner.py --image durante-isolation-test \
  --validation-bin .results/owner-bin --launcher /trusted/isolation-tool/bwrap \
  --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 --client build/libdurante.so \
  --output-dir .results/execution-owner
```

Use a fresh output directory and a local Docker daemon whose cgroup v2 hierarchy
the owner can inspect. An unavailable environment or failed positive control
fails the suite; it is never reported as successful fault coverage. Each case
retains the controller observation, bounded output/report, cgroup evidence and
owner outcome. Source, fixture and installation hashes must be unchanged across
the run. `results.json` must report all five cases passed and complete
cleanup. Failed/killed/stopped cases always have `accepted_counts: false`.

This does not qualify a crashed external owner, a failed Docker daemon, host
reboots, disk I/O latency, memory OOM handling, or arbitrary hostile workloads.
The storage case covers controller preparation, not all mid-run publication
failures. Counters remain application-mutable and diagnostic, including after
successful containment. Follow [operator recovery](OPERATIONS.md) and the
[isolation prerequisites](ISOLATION.md) for production decisions.

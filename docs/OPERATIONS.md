# Operator checks and result handling

Run `doctor` in the same environment and with the same installation paths that
will run the workload. It prints a schema-1 JSON diagnostic to stdout and does
not execute the SDK launcher, isolation launcher or a target. It does not create
cgroups/namespaces, change sysctls/security policy, repair installations, or
write report/workspace files.

For isolated execution, the [external execution-owner guide](EXECUTION_OWNER.md)
defines who enforces aggregate limits and removes the entire job after controller
failure. Its recovery tests and documented limits complement the installation
checks below; a successful `doctor` result does not test owner recovery.

```sh
durante doctor --drrun /trusted/sdk/bin64/drrun --client /trusted/libdurante.so
durante doctor --profile isolate --drrun /trusted/sdk/bin64/drrun \
  --client /trusted/libdurante.so --launcher /trusted/bwrap
```

Without paths, `doctor` still reports environment checks, and marks the missing
installation checks `not_checked`. Checks have stable `id`, `required`, `status`,
`detail` and `remedy` fields. Status is `pass`, `fail` or `not_checked`. Exit 0
means every required check passed; exit 1 means a required check failed/was not
checked, or JSON could not be written. Command-line syntax errors exit 2.
Inspect the JSON rather than depending on detail text. For example, this is a
**selected-field excerpt**, not a complete report:

```json
{
  "schema": 1,
  "command": "doctor",
  "profile": "isolate",
  "checks_passed": false,
  "execution_tested": false,
  "checks": [
    {"id": "isolation_launcher", "required": true, "status": "not_checked"},
    {"id": "namespace_creation_policy", "required": false, "status": "not_checked"}
  ]
}
```

Both profiles check `close_range(CLOSE_RANGE_CLOEXEC)` using an empty descriptor
range, installation permissions, and the prepared SDK's pinned provenance and
release-core hash. This SDK consistency check is stricter than `run`'s path-only
launch check. It does not authenticate the installer or prove executable
compatibility. The isolate profile also uses the actual launcher's manifest
validator and cgroup-limit validator, checks the CLI's installation, namespace
interface visibility and the user-namespace limit. Individual failures do not
prevent reporting the other checks. Sensitive supplied paths are not echoed.

`checks_passed: true` is a point-in-time prerequisite result, not permission to
run untrusted code. `execution_tested` is always false: LSM/seccomp policy and
actual namespace creation, workload compatibility, stdin/stdout handles,
workspace copying, destination publication, aggregate resource ownership and
controller-death teardown still need execution-level validation. Advisory checks
remain `not_checked` even when all required checks pass. The command changes no
host policy to make them pass. Follow the [isolation prerequisites](ISOLATION.md)
and keep the external execution owner in place.

## Reading an execution result

The CLI status and target status have different meanings. A normally exiting
target with exit code 7 can produce CLI exit 0 and complete diagnostic counts.
`run` stores the target status in `execution.exit_code`; `isolate` stores its
wrapper status in `execution.wrapper_exit_code` and the accepted target result
inside `untrusted_inner_report`. The latter remains application-mutable.

These are selected-field excerpts; omitted fields must still be validated by a
consumer according to the [measurement contract](PROTOCOL.md#result-contract-and-consumers).

Normal `run` completion with a nonzero target exit:

```json
{
  "authority": "diagnostic_only",
  "integrity_verified": false,
  "execution": {"state": "complete", "exit_code": 7, "cleanup_complete": true},
  "measurement": {"valid": true, "complete": true}
}
```

An isolated successful execution has `execution.state: "complete"`,
`execution.wrapper_exit_code: 0`, successful process/scratch cleanup and an
`untrusted_inner_report` with complete inner execution and measurement. With
capture, separately require `output.published` and `output.complete` for full
output. An EOF-complete output can accompany a timed-out execution; output
completeness is not execution success. An exact quota followed by EOF is allowed.

A failed isolated execution (CLI exit 1) can retain inner termination diagnostics:

```json
{
  "execution": {"state": "isolation_failed", "wrapper_exit_code": 1},
  "untrusted_failure_reason": "root did not exit normally",
  "untrusted_failure_execution": {
    "state": "incomplete",
    "root_pid": 3,
    "exit_code": null,
    "signal": 15
  }
}
```

`untrusted_failure_execution` contains only the inner execution state, guest
root PID, exit code and signal. A null exit code or signal means the inner
supervisor did not observe that form of termination. Normal `exit(139)` is
distinct from termination by signal 11: never infer a signal from `128 + signal`
arithmetic on an exit code. These fields remain untrusted diagnostics; they do
not provide accepted counts or turn a failed result into a successful one.
No `untrusted_inner_report` is accepted in this example. Failure diagnostics are
optional and can be absent after missing or malformed inner reports, deadlines
or forced termination.

An output quota stop (CLI exit 125) can retain a published prefix:

```json
{
  "execution": {"state": "output_limit", "cleanup_complete": true},
  "output": {"mode": "captured", "truncated": true, "published": true, "complete": false}
}
```

There is no accepted `untrusted_inner_report` after this stop. A timeout (CLI
exit 124, `execution.state: "timed_out"`) likewise exposes no accepted inner
report. `run` instead includes an invalid `measurement` without aggregate
counts. Neither stopped state should be interpreted as a target's normal exit.

## Failure and recovery decisions

| Observation | Caller action |
| --- | --- |
| No report or an empty/unparseable report | Treat execution/measurement as unavailable. Preflight can fail before report creation; forced controller death can prevent publication. Retain the CLI status and bounded stderr separately. |
| `incomplete` or an invalid counter report | Do not consume aggregate counts. Keep bounded diagnostics and investigate unsupported execution paths or malformed records. |
| `output_failed` or `output.published: false` | Treat output as unavailable/partial even if files exist. Publication failures can leave partial files. |
| `cleanup_failed` or a false cleanup field | Have the external owner enforce teardown of the entire execution group; do not infer that all descendants are gone. |
| `execution.records_dir` or `execution.retained_scratch` | Retain the indicated artifacts for investigation. Only clean them after the owner confirms execution has stopped and verifies ownership/path safety. |
| Replaced report/output paths | Preserve the failure evidence and choose a fresh protected destination. The CLI does not overwrite or delete the suspicious path to recover. |

Reports use fresh destinations. Recovery must not recursively delete a path
supplied by an untrusted report or reuse a possibly compromised parent directory.
All target text and inner reports are untrusted. Normal exits, valid totals,
successful containment and doctor passes keep `trusted_verdict_eligible: false`;
none establishes trusted CPU time, TLE or billing authority.
